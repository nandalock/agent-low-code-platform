"""崩溃修复 —— 冷恢复时把**未闭合的尾巴**补平（对齐 DSH ``session/repair.ts``）

Event Log 是执行事实的唯一来源，而它可能被**半途截断**：进程被 kill、容器被重启时，
最后一轮可能只有 ``turn/start`` 没有 ``turn/end``；``assistant/message`` 里的 tool_calls
可能永远等不到配对的 ``tool/result``。这样的日志**物理上合法**（每条都是真发生过的事），
但语义上不完整 —— 直接拿去继续对话会得到非法消息序（assistant.tool_calls 缺配对的
tool 消息，OpenAI 兼容网关直接 400），trace 也永远收不了口。

B 里什么时候会真的出现这种尾巴：事件在 **turn 末**批量 flush，但 flush 不止一个触发点
（见 persistence/base.py 与 title/service.py）—— 后台标题生成、同会话的另一轮，都可能在
某一轮**进行中**把它的前缀落库。此刻进程挂掉，库里就留下一个未闭合的 turn。

本模块只做一件事：**扫描事件流，产出把尾巴闭合所需的合成事件**。它是纯函数 ——
不读存储、不碰 Session，由装配层（AgentRuntime 冷恢复）调用后落库。与 A 的分层一致：
persistence 只保证物理合法，"语义修复是 agent 层的事"。

合成事件的两条硬规矩（照搬 A 的取舍）：
  1. **不发明未来时间**：seq 续在最后一条事件之后，time 复用最后一条事件的 time ——
     否则 trace 会把「两天前那次崩溃」算成一段巨大的 latency；
  2. **不发明工具结果**：合成结果如实说明「结果未知」（已发起过）或「未执行」
     （连发起都没记上），并把「要不要重试」的判断交还给模型 —— 绝不假装调用成功。

``sourceEventSeqs``（B 是 ``SessionEvent.source_event_seqs``，一直是保留扩展位）在这里
第一次被用上：合成结果**引用**它所补的那个 ``tool/call`` 的 seq，回放时「这条结果是补的、
补的是哪次调用」一眼可见。
"""
import json

from backend.agents.runtime.session.events import (
    ASSISTANT_MESSAGE,
    STEP_END,
    STEP_START,
    TOOL_CALL,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    SessionEvent,
)

#: 恢复码（写进合成 tool/result 的 content，机器可读 —— 与 A 的两个 code 同名）
TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"   # 已记发起，结果没落库
TOOL_NOT_STARTED = "TOOL_NOT_STARTED"           # 连发起都没记上：没执行过

#: 被中断的 turn 的收口原因（stop_reason 词表取值）。与 ``cancelled`` 刻意分开：
#: cancelled 是「有驱动方让它停」（用户点了停止 / 连接断开），interrupted 是「进程没了」——
#: 前者有人负责，后者要运维去看。
STOP_REASON_INTERRUPTED = "interrupted"

#: 模型可见的合成结果文本（判断交还给模型：能不能重试取决于工具的副作用语义）
_OUTCOME_UNKNOWN_TEXT = (
    "这次运行意外中断：该工具调用已发起，但结果没能落库，结果未知。"
    "是否重试请按工具语义判断 —— 只读或幂等的可以重试；可能有副作用的，"
    "先核实外部状态或询问用户，不要盲目重试。"
)
_NOT_STARTED_TEXT = (
    "这次运行意外中断：该工具调用尚未执行。若仍然需要，请重新调用。"
)


def interrupted_turn_closers(events: list[SessionEvent]) -> list[SessionEvent]:
    """扫描事件流，返回补平未闭合尾巴所需的合成事件；日志已平衡则返回空列表。

    单遍扫描维护两组游标（对齐 A 的 ``interruptedTurnClosers``）：

      ``open_turn`` / ``open_step`` —— 最近一次 turn/start、step/start 是否已被闭合；
      ``pending``                   —— assistant/message 里出现、还没等到 tool/result 的调用。

    ``turn/end`` 与 ``step/end`` 都会清空 ``pending``：跨边界的调用不该被尾巴修复牵连
    （一个已闭合的 step 里没配对的调用是别的问题，这里不掩盖它）。

    返回顺序即写入顺序 —— **先补结果、再关 step、最后关 turn**：turn/end 落在 step 开着
    的时候是不变式违规，所以 step 的边界必须先合成。
    """
    open_turn = False          # turn/start 见过、还没见到 turn/end
    open_step: int | None = None
    # tool_call_id → {call_seq, tool}；dict 保序 = 模型给出的调用顺序
    pending: dict[str, dict] = {}

    for ev in events:
        t, d = ev.type, ev.data
        if t == TURN_START:
            open_turn, open_step = True, None
            pending.clear()
        elif t == TURN_END:
            open_turn, open_step = False, None
            pending.clear()
        elif t == STEP_START:
            open_step = int(d.get("step", 1))
        elif t == STEP_END:
            open_step = None
            pending.clear()
        elif t == ASSISTANT_MESSAGE:
            for tc in d.get("tool_calls") or []:
                call_id = tc.get("id")
                if not call_id:
                    continue
                fn = tc.get("function") or {}
                pending[call_id] = {"call_seq": None, "tool": fn.get("name") or ""}
        elif t == TOOL_CALL:
            entry = pending.get(d.get("tool_call_id") or "")
            if entry is not None:
                entry["call_seq"] = ev.seq
                if d.get("tool"):
                    entry["tool"] = d["tool"]     # tool/call 记的是真名，优先于 LLM 侧安全名
        elif t == TOOL_RESULT:
            pending.pop(d.get("tool_call_id") or "", None)
        # 其余事件（user/message、chunk、llm/usage、tool/progress…）不移动边界游标

    last = events[-1] if events else None
    if not open_turn or last is None:
        return []      # 已平衡 / 空日志：无尾巴可补

    # seq 与 time 都从最后一条事件续（见模块头「不发明未来时间」）
    seq = last.seq
    closers: list[SessionEvent] = []

    def _emit(type_: str, data: dict, source_seqs: list[int] | None = None) -> None:
        nonlocal seq
        seq += 1
        closers.append(SessionEvent(
            type=type_, seq=seq, time=last.time, data=data, source_event_seqs=source_seqs,
        ))

    for call_id, entry in pending.items():
        started = entry["call_seq"] is not None
        result = {
            "error": _OUTCOME_UNKNOWN_TEXT if started else _NOT_STARTED_TEXT,
            "code": TOOL_OUTCOME_UNKNOWN if started else TOOL_NOT_STARTED,
        }
        _emit(
            TOOL_RESULT,
            {
                "tool": entry["tool"],
                "tool_call_id": call_id,
                # 与 SessionToolRecorder 同形：content 是 JSON 全文（供模型阅读）
                "content": json.dumps(result, ensure_ascii=False),
            },
            [entry["call_seq"]] if started else None,
        )

    if open_step is not None:
        _emit(STEP_END, {"step": open_step})
    _emit(TURN_END, {"stop_reason": STOP_REASON_INTERRUPTED})
    return closers
