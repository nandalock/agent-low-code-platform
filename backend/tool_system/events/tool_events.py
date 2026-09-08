"""ToolEvent — Tool 生命周期事件（Runtime 层能力）

产生方与去向：
    ToolRuntime  → tool.started / tool.completed / tool.failed
    Executor     → tool.progress（长任务阶段进度）
        ↓  context.event_sink.emit(event)
    装配层（当前：Session Event Log 的 tool/progress + 可选 UI 事件流）

事件是纯数据：不持有 Session / SessionStore / 数据库连接，也不直接写任何存储。
没有 event_sink（或没有 context）时，Runtime / Executor 不产生任何事件。
"""
import time
from dataclasses import dataclass, field
from typing import Protocol

TOOL_STARTED = "tool.started"
TOOL_PROGRESS = "tool.progress"
TOOL_COMPLETED = "tool.completed"
TOOL_FAILED = "tool.failed"


@dataclass
class ToolEvent:
    """一次 Tool 执行生命周期中的事件（type 取上面的常量）"""

    type: str
    tool_name: str
    session_id: str | None = None
    trace_id: str | None = None
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """UI / SSE 友好的平铺结构"""
        return {
            "type": self.type,
            "tool": self.tool_name,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }


class ToolEventSink(Protocol):
    """事件出口（装配层实现）。约定 async：实现方可 await 落库 / 推送。"""

    async def emit(self, event: ToolEvent) -> None: ...
