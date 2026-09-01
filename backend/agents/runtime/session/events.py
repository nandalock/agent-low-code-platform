"""Session 事件定义（DSH 思想：Session Event Log 是执行事实来源）

事件类型与 LLM messages 的映射由 Surface + derive_event_message 负责：
  - surface 事件（进入 LLM Context）: user/message, assistant/message, tool/result
  - 过程事件（仅 Event Log，不进入 LLM）: turn/start, step/start, assistant/chunk,
    tool/call, step/end, turn/end

按规格暂不实现：steering/message、todo/write、request/header、compaction、delegation。
"""
import uuid
from dataclasses import dataclass

# ── 事件类型常量 ──
TURN_START = "turn/start"
TURN_END = "turn/end"
STEP_START = "step/start"
STEP_END = "step/end"
USER_MESSAGE = "user/message"
ASSISTANT_CHUNK = "assistant/chunk"      # 流式过程事件（data: kind=thinking|text + delta），不直接进 LLM
ASSISTANT_MESSAGE = "assistant/message"  # 完整 assistant message（含 tool_calls）
TOOL_CALL = "tool/call"
TOOL_RESULT = "tool/result"

# 进入 LLM Context 的 surface 事件（其余事件只存在于 Event Log）
SURFACE_EVENT_TYPES = frozenset({USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT})


@dataclass
class SessionEvent:
    """一条 Session 执行事实。data 结构由事件类型约定（见各 append 调用点）。"""
    type: str
    seq: int                                  # 全局递增序号（排序依据）
    time: float                               # 事件发生时间（wall clock）
    data: dict
    source_event_seqs: list[int] | None = None  # 派生来源（保留扩展位，暂无派生事件）


@dataclass
class SessionHeader:
    """Session 元信息。id / created_at 由 Session 创建时自动生成。"""
    version: int
    id: str
    created_at: float
    cwd: str | None = None
    parent_session: str | None = None
    seed_length: int | None = None
    delegation_depth: int | None = None


def new_session_id() -> str:
    return uuid.uuid4().hex
