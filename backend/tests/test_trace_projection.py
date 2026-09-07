"""TraceProjection 投影测试（纯内存，无 DB 依赖）

覆盖目标（Session Event Log → Trace Projection → AgentReply.trace 的派生层）：
  A   空事件 / 空轮 → {"steps": []}（旧 trace 初始形状）
  B   单轮 llm step：entry 形状（0-based step / content / latency / per-step usage）、
      limits、usage 聚合、stop_reason（completed —— 有意对齐：现在始终存在）
  C   llm 无 usage / usage 无 cache 字段 / ratio 四舍五入
  D   tool 完整往返：args 来自 tool/call（按 call_id）、output 为 json.loads 结果
  E   同 step 多 tool、乱序 result 回填、malformed JSON 容错
  F   guard 序列（max_steps / max_tool_calls / repeat_tool / max_wall_time）与
      error 轮、无 turn/end、旧数据（无 limits）、0-based 编号
  G   streaming（assistant/chunk）不产生重复 entry
  H   缺配对容错（孤儿 tool/call、无 step/start 的 assistant）
  I   多轮 fold 收敛到最后一轮；增量 == 回放同构
  J   真 Session listener 生命周期（attach / detach / from_events 冷恢复对账）
  K   latency 由 SessionEvent.time 差推导（clamp ≥ 0）

AgentReply.trace / Persistence flush / SSE done.trace 的端到端行为由
test_session_persistence.py 与手动 smoke 覆盖（本项目无 trace 内容断言测试）。

运行方式（backend 容器内）:
  python -m pytest backend/tests/test_trace_projection.py -v
"""
import json

from backend.agents.runtime.session.events import SessionEvent
from backend.agents.runtime.session.trajectory_projection import project_trajectory
from backend.agents.runtime.session.session import Session
from backend.agents.runtime.session.trace_projection import TraceProjection, project_trace

LIMITS = {"max_steps": 5, "max_tool_calls": 30, "max_wall_time": 300.0}
USAGE_4 = {
    "prompt_tokens": 10,
    "completion_tokens": 5,
    "prompt_cache_hit_tokens": 80,
    "prompt_cache_miss_tokens": 20,
}


class _TSeq:
    """带递增 wall-clock 的事件构造器（latency 由事件时间差推导；dt 默认 1.0s）"""

    def __init__(self):
        self.n = 0
        self.t = 0.0

    def ev(self, type_: str, data: dict, dt: float = 1.0) -> SessionEvent:
        self.n += 1
        self.t += dt
        return SessionEvent(type=type_, seq=self.n, time=self.t, data=data)


def _run(events: list[SessionEvent]) -> dict:
    """折叠 handle：返回投影终态（snapshot）"""
    p = TraceProjection()
    for ev in events:
        p.handle(ev)
    return p.snapshot()


def _turn_start(s: _TSeq) -> list[SessionEvent]:
    return [s.ev("turn/start", {"agent": "test", "limits": dict(LIMITS)})]


def _llm_step(s: _TSeq, content: str = "你好", usage: dict | None = None,
              step: int = 1, tool_calls: list | None = None) -> list[SessionEvent]:
    events = [s.ev("step/start", {"step": step, "total": 5})]
    events.append(s.ev("assistant/message", {
        "content": content, "tool_calls": tool_calls or [],
    }))
    if usage:
        events.append(s.ev("llm/usage", {"step": step, **usage}))
    events.append(s.ev("step/end", {"step": step}))
    return events


def _tool_roundtrip(s: _TSeq, call_id: str, tool: str = "order_query",
                    args: dict | None = None, result: dict | None = None) -> list[SessionEvent]:
    return [
        s.ev("tool/call", {
            "tool": tool, "args": args or {"tenant_id": 1}, "tool_call_id": call_id,
        }),
        s.ev("tool/result", {
            "tool": tool, "tool_call_id": call_id,
            "content": json.dumps(result if result is not None else {"rows": [1]}, ensure_ascii=False),
        }),
    ]


# ── A: 空事件 / 空轮 ──

def test_empty_events():
    assert _run([]) == {"steps": []}


def test_config_missing_early_return_turn_empty():
    """缺配置早退：无 turn 事件（Runtime 建了 Session 但 AgentLoop 直接 return）"""
    s = _TSeq()
    assert _run(_turn_start(s)) == {"steps": [], "limits": LIMITS}


# ── B: 单轮 llm + usage（完整形状）──

def test_single_turn_llm_with_usage():
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "回答", usage=USAGE_4)
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    snap = _run(events)

    assert snap["steps"] == [{
        "step": 0,
        "type": "llm",
        "content": "回答",
        "latency_ms": 1000,  # assistant/message 与 step/start 相差 1.0s
        "usage": dict(USAGE_4),
    }]
    assert snap["limits"] == LIMITS
    assert snap["usage"] == {
        "llm_calls": 1,
        "cache_hit_tokens": 80,
        "cache_miss_tokens": 20,
        "cache_hit_ratio": 0.8,
    }
    assert snap["stop_reason"] == "completed"  # 有意对齐：现在始终存在


def test_llm_step_without_usage():
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "无 usage")
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    snap = _run(events)
    assert "usage" not in snap["steps"][0]
    assert "usage" not in snap
    assert "latency_ms" in snap["steps"][0]


# ── C: usage 聚合边界 ──

def test_usage_aggregate_no_cache_fields():
    """两个 cache 字段都缺（非 DeepSeek 网关）→ ratio None，仅计数"""
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "x", usage={
        "prompt_tokens": 10, "completion_tokens": 5,
        "prompt_cache_hit_tokens": None, "prompt_cache_miss_tokens": None,
    })
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    snap = _run(events)
    assert snap["usage"] == {
        "llm_calls": 1,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "cache_hit_ratio": None,
    }


def test_usage_aggregate_ratio_rounding():
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "x", usage={
        "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 7,
    })
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    snap = _run(events)
    assert snap["usage"]["cache_hit_ratio"] == round(3 / 10, 4)


def test_usage_aggregate_multiple_calls():
    s = _TSeq()
    events = _turn_start(s)
    events += _llm_step(s, "a", usage={"prompt_cache_hit_tokens": 30, "prompt_cache_miss_tokens": 10}, step=1)
    events += _llm_step(s, "b", usage={"prompt_cache_hit_tokens": 10, "prompt_cache_miss_tokens": 50}, step=2)
    events.append(s.ev("turn/end", {"stop_reason": "max_steps"}))
    snap = _run(events)
    assert snap["usage"] == {
        "llm_calls": 2,
        "cache_hit_tokens": 40,
        "cache_miss_tokens": 60,
        "cache_hit_ratio": 0.4,
    }
    # per-step：两条 llm entry 各带自己的 usage
    assert [e["usage"]["prompt_cache_hit_tokens"] for e in snap["steps"]] == [30, 10]


# ── D/E: tool step ──

def test_tool_step_full_roundtrip():
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "", tool_calls=[{"id": "c1", "function": {"name": "order_query"}}])
    events += _tool_roundtrip(s, "c1", args={"order_id": "A1", "tenant_id": 1}, result={"rows": [{"ok": True}]})
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    snap = _run(events)

    assert len(snap["steps"]) == 2
    llm_e, tool_e = snap["steps"]
    assert llm_e["type"] == "llm" and tool_e["type"] == "tool"
    assert tool_e["step"] == 0                       # 0-based
    assert tool_e["tool"] == "order_query"
    assert tool_e["args"] == {"order_id": "A1", "tenant_id": 1}  # 来自 tool/call
    assert tool_e["output"] == {"rows": [{"ok": True}]}          # json.loads 还原
    assert tool_e["latency_ms"] == 1000
    assert "usage" not in llm_e


def test_tool_result_malformed_json():
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "", tool_calls=[{"id": "c1"}])
    events += [
        s.ev("tool/call", {"tool": "t", "args": {}, "tool_call_id": "c1"}),
        s.ev("tool/result", {"tool": "t", "tool_call_id": "c1", "content": "not json"}),
    ]
    snap = _run(events)
    assert snap["steps"][1]["output"] == "not json"  # 容错：原样字符串


def test_multi_tool_out_of_order_results():
    """同 step 多 tool：result 乱序回填仍按 call_id 匹配 args"""
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "", tool_calls=[
        {"id": "c1"}, {"id": "c2"},
    ])
    events.append(s.ev("tool/call", {"tool": "tool_a", "args": {"k": "a"}, "tool_call_id": "c1"}))
    events.append(s.ev("tool/call", {"tool": "tool_b", "args": {"k": "b"}, "tool_call_id": "c2"}))
    # 结果乱序：先回 b，再回 a
    events.append(s.ev("tool/result", {"tool": "tool_b", "tool_call_id": "c2", "content": json.dumps({"rows": ["B"]})}))
    events.append(s.ev("tool/result", {"tool": "tool_a", "tool_call_id": "c1", "content": json.dumps({"rows": ["A"]})}))
    snap = _run(events)
    tool_entries = [e for e in snap["steps"] if e["type"] == "tool"]
    assert [e["tool"] for e in tool_entries] == ["tool_b", "tool_a"]  # 事件到达序
    assert tool_entries[0]["args"] == {"k": "b"}
    assert tool_entries[0]["output"] == {"rows": ["B"]}
    assert tool_entries[1]["args"] == {"k": "a"}


# ── F: guard 序列 ──

def test_max_steps_guard():
    s = _TSeq()
    events = _turn_start(s)
    events += _llm_step(s, "a", step=1)
    events += _llm_step(s, "b", step=2)
    events.append(s.ev("turn/end", {"stop_reason": "max_steps"}))
    snap = _run(events)
    assert [e["step"] for e in snap["steps"]] == [0, 1]
    assert snap["stop_reason"] == "max_steps"


def test_max_tool_calls_guard():
    """guard 在 tool/call append 之前 break → 只有 llm entry 没有 tool entry"""
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "", tool_calls=[{"id": "c1"}])
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "max_tool_calls"}))
    snap = _run(events)
    assert [e["type"] for e in snap["steps"]] == ["llm"]
    assert snap["stop_reason"] == "max_tool_calls"


def test_repeat_tool_guard():
    """连续重复 3 次后被停：第 3 次调用有完整 roundtrip（第 4 次不会有 tool/call）"""
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "", tool_calls=[{"id": "c0"}])
    for i in range(3):
        events += _tool_roundtrip(s, f"c{i + 1}", tool="same_tool")
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "repeat_tool"}))
    snap = _run(events)
    assert len([e for e in snap["steps"] if e["type"] == "tool"]) == 3
    assert snap["stop_reason"] == "repeat_tool"


def test_wall_time_guard_no_step_events():
    """wall_time 耗尽发生在 step/start 之前：无 step 事件，只有 turn 收口"""
    s = _TSeq()
    events = _turn_start(s)
    events.append(s.ev("turn/end", {"stop_reason": "max_wall_time"}))
    snap = _run(events)
    assert snap["steps"] == []
    assert snap["stop_reason"] == "max_wall_time"
    assert snap["limits"] == LIMITS


def test_error_turn_no_assistant_message():
    """LLM 调用异常：无 assistant/message，turn/end=error → stop_reason=error"""
    s = _TSeq()
    events = _turn_start(s)
    events.append(s.ev("step/start", {"step": 1, "total": 5}))
    events.append(s.ev("turn/end", {"stop_reason": "error"}))
    snap = _run(events)
    assert snap["steps"] == []
    assert snap["stop_reason"] == "error"  # 有意对齐：旧代码此场景无此 key


def test_no_turn_end_no_stop_reason():
    s = _TSeq()
    snap = _run(_turn_start(s) + _llm_step(s, "中途中"))
    assert "stop_reason" not in snap


def test_limits_absent_old_persisted_events():
    """旧数据兼容：重构前的 turn/start 没有 limits 字段"""
    s = _TSeq()
    events = [s.ev("turn/start", {"agent": "test"})] + _llm_step(s, "旧事件")
    snap = _run(events)
    assert "limits" not in snap
    assert len(snap["steps"]) == 1


def test_zero_based_step_numbering():
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "x", step=3)
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    snap = _run(events)
    assert snap["steps"][0]["step"] == 2  # 事件 1-based → trace 0-based


# ── G: streaming chunk 不产生重复 entry ──

def test_streaming_chunks_single_entry():
    """assistant/chunk（thinking/text）只广播不建 entry；assistant/message 收口为一条"""
    s = _TSeq()
    events = _turn_start(s)
    events.append(s.ev("step/start", {"step": 1, "total": 5}))
    events.append(s.ev("assistant/chunk", {"kind": "thinking", "delta": "推理中"}, dt=0))
    events.append(s.ev("assistant/chunk", {"kind": "text", "delta": "你"}, dt=0))
    events.append(s.ev("assistant/chunk", {"kind": "text", "delta": "好"}, dt=0))
    events.append(s.ev("assistant/message", {"content": "你好", "tool_calls": []}))
    events.append(s.ev("step/end", {"step": 1}, dt=0))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}, dt=0))
    snap = _run(events)
    assert len(snap["steps"]) == 1
    assert snap["steps"][0] == {
        "step": 0, "type": "llm", "content": "你好", "latency_ms": 1000,
    }


def test_thinking_only_step_content_empty():
    """纯思考步（content 空）：llm entry content 为 ""（旧代码 or "" 语义）"""
    s = _TSeq()
    events = _turn_start(s)
    events.append(s.ev("step/start", {"step": 1, "total": 5}))
    events.append(s.ev("assistant/message", {"content": "", "tool_calls": []}))
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    snap = _run(events)
    assert snap["steps"][0]["content"] == ""


# ── H: 缺配对容错 ──

def test_assistant_without_step_start():
    s = _TSeq()
    events = [s.ev("assistant/message", {"content": "孤", "tool_calls": []})]
    snap = _run(events)
    assert snap["steps"] == [{"step": 0, "type": "llm", "content": "孤"}]
    assert "latency_ms" not in snap["steps"][0]


def test_tool_result_without_tool_call():
    s = _TSeq()
    events = [s.ev("tool/result", {"tool": "t", "tool_call_id": "c9", "content": json.dumps({"rows": [1]})})]
    snap = _run(events)
    assert snap["steps"] == [{
        "step": 0, "type": "tool", "tool": "t", "args": {}, "output": {"rows": [1]},
    }]
    assert "latency_ms" not in snap["steps"][0]


def test_orphan_tool_call_no_trace_entry():
    """孤儿 tool/call（turn 被异常打断）：不产生 tool entry（旧行为一致）"""
    s = _TSeq()
    events = _turn_start(s)
    events.append(s.ev("step/start", {"step": 1, "total": 5}))
    events.append(s.ev("tool/call", {"tool": "t", "args": {}, "tool_call_id": "c1"}))
    events.append(s.ev("turn/end", {"stop_reason": "error"}))
    snap = _run(events)
    assert snap["steps"] == []


def test_llm_usage_without_llm_entry():
    """usage 无对应 llm entry：聚合照记，不崩溃、不挂 usage"""
    s = _TSeq()
    events = _turn_start(s)
    events.append(s.ev("llm/usage", {"step": 1, **USAGE_4}))
    events.append(s.ev("turn/end", {"stop_reason": "error"}))
    snap = _run(events)
    assert snap["steps"] == []
    assert snap["usage"]["llm_calls"] == 1
    assert snap["usage"]["cache_hit_ratio"] == 0.8


# ── K: latency 推导 ──

def test_latency_from_event_time_deltas():
    s = _TSeq()
    events = [
        s.ev("turn/start", {"agent": "test"}),
        s.ev("step/start", {"step": 1, "total": 5}, dt=0.5),
        s.ev("assistant/message", {"content": "hi", "tool_calls": []}, dt=1.25),  # 距 step/start 1.25s
        s.ev("tool/call", {"tool": "t", "args": {}, "tool_call_id": "c1"}, dt=0.25),
        s.ev("tool/result", {"tool": "t", "tool_call_id": "c1", "content": json.dumps({"rows": [1]})}, dt=2.0),  # tool 2.0s
        s.ev("step/end", {"step": 1}),
        s.ev("turn/end", {"stop_reason": "completed"}),
    ]
    snap = _run(events)
    llm_e, tool_e = snap["steps"]
    assert llm_e["latency_ms"] == 1250
    assert tool_e["latency_ms"] == 2000


def test_latency_clamped_non_negative():
    """wall clock 倒退（事件时间差为负）→ clamp 0 而非负值"""
    s = _TSeq()
    ev1 = s.ev("step/start", {"step": 1, "total": 5})
    ev2 = SessionEvent(type="assistant/message", seq=ev1.seq + 1, time=ev1.time - 10.0,
                       data={"content": "x", "tool_calls": []})
    snap = _run([ev1, ev2])
    assert snap["steps"][0]["latency_ms"] == 0


# ── I: 多轮 / 同构 ──

def test_multi_turn_fold_returns_last_turn():
    """多轮 session fold → 最后一轮 trace（每 reply() = 一个 trace = 一轮）"""
    s1, s2 = _TSeq(), _TSeq()
    turn1 = _turn_start(s1) + _llm_step(s1, "第一轮回答", usage=USAGE_4)
    turn1.append(s1.ev("turn/end", {"stop_reason": "completed"}))
    turn2 = _turn_start(s2) + _llm_step(s2, "第二轮回答")
    turn2.append(s2.ev("turn/end", {"stop_reason": "completed"}))

    snap = _run(turn1 + turn2)
    assert len(snap["steps"]) == 1
    assert snap["steps"][0]["content"] == "第二轮回答"
    assert "usage" not in snap  # 第二轮无 usage → 聚合清零（turn/start 重置）

    # 同构：多轮 fold 的最后一轮 == 只喂第二轮事件的单轮投影
    assert _run(turn2) == snap


def test_fold_equals_incremental():
    """project_trace(events) == 逐条 handle（严格同构）"""
    s = _TSeq()
    events = _turn_start(s)
    events += _llm_step(s, "回答", usage=USAGE_4, tool_calls=[{"id": "c1"}])
    events += _tool_roundtrip(s, "c1", result={"rows": [1, 2]})
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    assert project_trace(events) == _run(events)


def test_cold_restore_fold_parity():
    """冷恢复对账：真实 Session 事件 → from_events 重建 → fold 与增量同构"""
    session = Session(init_messages=[{"role": "system", "content": "sys"}])
    p = TraceProjection()
    session.add_listener(p.handle)

    session.append("turn/start", {"agent": "test", "limits": dict(LIMITS)})
    session.append("step/start", {"step": 1, "total": 5})
    session.append("assistant/message", {"content": "你好", "tool_calls": []})
    session.append("llm/usage", {"step": 1, **USAGE_4})
    session.append("step/end", {"step": 1})
    session.append("turn/end", {"stop_reason": "completed"})

    live = p.snapshot()
    assert live == project_trace(session.events)

    # from_events 重建（无 listener）→ fold 得相同 trace（冷恢复路径）
    restored = Session.from_events(session.header, session.events)
    assert project_trace(restored.events) == live


# ── J: Session listener 生命周期 ──

def test_session_listener_lifecycle():
    """attach 期间收到事件；detach 后不再变化；重复 add 不重复消费"""
    session = Session(init_messages=[{"role": "system", "content": "sys"}])
    p = TraceProjection()
    session.add_listener(p.handle)
    session.add_listener(p.handle)  # identity 去重：不重复注册

    session.append("turn/start", {"agent": "test", "limits": dict(LIMITS)})
    session.append("step/start", {"step": 1, "total": 5})
    session.append("assistant/message", {"content": "好", "tool_calls": []})
    snap_mid = p.snapshot()

    session.remove_listener(p.handle)
    session.append("step/end", {"step": 1})
    session.append("turn/end", {"stop_reason": "completed"})

    assert p.snapshot() == snap_mid  # 摘除后不再变化（不跨轮泄漏）
    # 真实 Session 用 wall clock，连续 append 间隔≈0 → 只断言形状与 key 存在
    entry = snap_mid["steps"][0]
    assert set(entry) == {"step", "type", "content", "latency_ms"}
    assert (entry["step"], entry["type"], entry["content"]) == (0, "llm", "好")
    # 全量 Event Log fold（含摘除后的事件）是另一回事：turn/end 收口完整
    full = project_trace(session.events)
    assert full["stop_reason"] == "completed"
    assert len(full["steps"]) == 1


# ── 对照：TrajectoryProjection 快照仍可用（防回归辅助） ──

def test_projection_snapshot_still_available():
    """TraceProjection 与 TrajectoryProjection 并存：同一事件流互不干扰"""
    s = _TSeq()
    events = _turn_start(s) + _llm_step(s, "你好", usage=USAGE_4)
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))
    traj = project_trajectory(events)
    assert traj["nodes"]
    # TrajectoryProjection 的 usage 列表原样保存 LLM_USAGE data（含 step key）
    assert traj["usage"] == [{"step": 1, **USAGE_4}]
