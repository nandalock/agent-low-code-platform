"""TraceProjection — Session typed events → Agent 执行 trace（Telemetry Projection）

Event Log 是 Agent 执行事实（真源）；本模块把 typed SessionEvent 投影为对外兼容的
trace dict（AgentReply.trace / done 事件 trace 的形状与旧 AgentLoop 直接维护的
trace 一致），是「Session Event → Trace Projection」这一派生消费者，不是第二套事实：
  - 不重新执行 Agent 行为 / 不回读 LLM，只消费已经发生的 SessionEvent。
  - 每轮（turn）一个 trace：turn/start 时重置全部状态 —— listener 只在一次
    reply() 期间挂载（天然只见当前轮）；project_trace() 对多轮 session fold 时
    收敛到最后一轮，二者严格同构（project_trace(events) == fold(handle)）。
  - timing（latency_ms）由 SessionEvent.time（wall clock）差推导并 clamp ≥ 0；
    total_ms 是运行时观测指标（含 session 装配/持久化开销），由 AgentRuntime
    在投影之外维护，不在此计算。
"""
import json

from backend.agents.runtime.session.events import (
    ASSISTANT_MESSAGE,
    LLM_USAGE,
    STEP_START,
    TOOL_CALL,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    SessionEvent,
)

# LLM_USAGE 事件 data 携带的 token 字段（per-step usage 投影固定取这 4 个）
_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)


class TraceProjection:
    """增量投影器：逐条消费 SessionEvent，维护 trace 内部状态。

    单实例服务一个「事件流消费端」（一次 reply() 的 Session listener / 一次回放）。
    snapshot() 产出与旧 AgentLoop trace 同形的 dict（wire 兼容，key 出现规则对齐
    旧代码的有条件写入：steps 恒在，limits/usage/stop_reason 有数据才出现）。
    """

    def __init__(self):
        self._limits: dict | None = None        # TURN_START data["limits"]
        self._steps: list[dict] = []            # 有序 trace step 条目（插入序 = 执行序）
        self._current_step: int | None = None   # 最近 step/start 的 step（1-based）
        self._step_start_time: float | None = None
        self._tool_calls: dict[str, dict] = {}  # tool_call_id → {tool, args, time}
        self._usage_calls = 0                   # 聚合：LLM_USAGE 事件数
        self._usage_hits = 0                    # 聚合：prompt_cache_hit_tokens 和
        self._usage_misses = 0                  # 聚合：prompt_cache_miss_tokens 和
        self._stop_reason: str | None = None    # TURN_END data["stop_reason"]

    # ── 外部接口 ──

    def handle(self, ev: SessionEvent) -> None:
        """消费一条 SessionEvent（Session listener 签名，忽略返回值）。"""
        t, d = ev.type, ev.data
        if t == TURN_START:
            # 每轮一个 trace：重置全部状态后取本轮的 limits（见模块 docstring）
            self._reset()
            self._limits = d.get("limits")
        elif t == STEP_START:
            self._current_step = int(d.get("step", 1))
            self._step_start_time = ev.time
        elif t == ASSISTANT_MESSAGE:
            entry = {
                "step": (self._current_step or 1) - 1,   # 0-based（旧 wire 形状）
                "type": "llm",
                "content": d.get("content") or "",
            }
            if self._step_start_time is not None:
                entry["latency_ms"] = self._latency_ms(ev.time - self._step_start_time)
            self._steps.append(entry)
        elif t == TOOL_CALL:
            call_id = d.get("tool_call_id")
            if call_id:
                self._tool_calls[call_id] = {
                    "tool": d.get("tool"),
                    "args": d.get("args", {}),
                    "time": ev.time,
                }
        elif t == TOOL_RESULT:
            call_id = d.get("tool_call_id")
            call = self._tool_calls.pop(call_id, None) if call_id else None
            content = d.get("content") or ""
            try:
                output = json.loads(content)  # content 是 json.dumps(tool_result)
            except (json.JSONDecodeError, TypeError):
                output = content              # 容错：非法 JSON 原样字符串
            entry = {
                "step": (self._current_step or 1) - 1,
                "type": "tool",
                "tool": d.get("tool") or ((call or {}).get("tool")),
                "args": (call or {}).get("args", {}),
                "output": output,
            }
            if call is not None:
                entry["latency_ms"] = self._latency_ms(ev.time - call["time"])
            self._steps.append(entry)
        elif t == LLM_USAGE:
            # 聚合（复刻旧 usage 汇总：int(x or 0) + ratio 规则）
            self._usage_calls += 1
            self._usage_hits += int(d.get("prompt_cache_hit_tokens") or 0)
            self._usage_misses += int(d.get("prompt_cache_miss_tokens") or 0)
            # per-step：AgentLoop 保证 LLM_USAGE 紧跟同 step 的 assistant/message
            for entry in reversed(self._steps):
                if entry.get("type") == "llm":
                    entry["usage"] = {k: d.get(k) for k in _USAGE_KEYS}
                    break
        elif t == TURN_END:
            self._stop_reason = d.get("stop_reason")
        # 其余类型（seed / user / chunk / progress / step-end）：no-op

    def snapshot(self) -> dict:
        """产出兼容旧 AgentLoop trace 的 dict（key 出现规则对齐旧有条件写入）。"""
        out = {"steps": list(self._steps)}
        if self._limits is not None:
            out["limits"] = self._limits
        if self._usage_calls:
            hits, misses = self._usage_hits, self._usage_misses
            out["usage"] = {
                "llm_calls": self._usage_calls,
                "cache_hit_tokens": hits,
                "cache_miss_tokens": misses,
                "cache_hit_ratio": round(hits / (hits + misses), 4) if (hits + misses) else None,
            }
        if self._stop_reason is not None:
            out["stop_reason"] = self._stop_reason
        return out

    # ── 内部 ──

    def _reset(self) -> None:
        """新一轮从零开始（turn/start 触发）。"""
        self.__init__()

    @staticmethod
    def _latency_ms(dt: float) -> int:
        """事件时间差 → 毫秒；负差（wall clock 抖动）clamp 为 0。"""
        return round(max(0.0, dt) * 1000)


def project_trace(events: list[SessionEvent]) -> dict:
    """回放 fold：project_trace(events) == 逐条 handle + snapshot（严格同构）。

    冷恢复路径：Session.from_events 重建（无 listener）→ 对历史 Event Log fold
    得历史 trace；与实时 listener 增量投影产出必须完全一致。
    """
    p = TraceProjection()
    for ev in events:
        p.handle(ev)
    return p.snapshot()
