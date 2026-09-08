"""SessionToolEventSink — ToolEvent 的装配层出口（Tool 事件 → Session Event Log / UI 流）

ToolRuntime / Executor 只负责 emit；「事件落到哪里」是装配层的事，本类给出当前装配：

  - tool.progress  → session.append(TOOL_PROGRESS)：沿用既有事件类型，
                     TrajectoryProjection / 持久化 / 前端轨迹时间线按原样工作
  - started / completed / failed → 转发给可选外部 EventSink（UI / SSE 流），不入 Event Log：
                     Session 里的 tool/call · tool/result 才是执行事实，避免重复记录

tool_call_id 在构造时绑定（一次 Tool 调用一个 sink）：Session 的 tool/progress
靠它与轨迹节点关联（TrajectoryProjection._find_tool）。
"""
import logging

from backend.agents.runtime.events import EventSink
from backend.agents.runtime.session import TOOL_PROGRESS, Session
from backend.tool_system.events import TOOL_PROGRESS as TOOL_PROGRESS_EVENT
from backend.tool_system.events import ToolEvent

logger = logging.getLogger(__name__)


class SessionToolEventSink:
    """ToolEvent 出口：进度入 Event Log，其余生命周期转发 UI 流"""

    def __init__(self, session: Session, tool_call_id: str, forward: EventSink | None = None):
        self._session = session
        self._tool_call_id = tool_call_id
        self._forward = forward

    async def emit(self, event: ToolEvent) -> None:
        if event.type == TOOL_PROGRESS_EVENT:
            self._session.append(TOOL_PROGRESS, {
                "tool": event.tool_name,
                "tool_call_id": self._tool_call_id,
                "stage": event.data.get("stage", ""),
                "seconds": event.data.get("seconds"),
            })
        if self._forward is not None:
            await self._forward(event.to_dict())
