"""SessionToolEventSink — ToolEvent 的装配层出口（Tool 事件 → Session Event Log / UI 流）

ToolRuntime / Executor 只负责 emit；「事件落到哪里」是装配层的事，本类给出当前装配：

  - tool.progress      → session.append(TOOL_PROGRESS)：沿用既有事件类型，
                         TrajectoryProjection / 持久化 / 前端轨迹时间线按原样工作
  - approval.request   → session.append(APPROVAL_REQUEST)：升权申请进入人工裁决。
                         「正在等谁批」这个状态必须可查——否则前端只会在裁决完成
                         后才看到结果，看不到等待过程
  - sandbox.escalation → session.append(SANDBOX_ESCALATION)：升权的批准/拒绝事实。
                         **记录，不是配置**——没有任何投影把它折叠成有效策略，
                         否则一次性授权会变成持久放权（见 session/events.py）
  - started / completed / failed → 转发给可选外部 EventSink（UI / SSE 流），不入 Event Log：
                         Session 里的 tool/call · tool/result 才是执行事实，避免重复记录

tool_call_id 在构造时绑定（一次 Tool 调用一个 sink）：Session 的 tool/progress
靠它与轨迹节点关联（TrajectoryProjection._find_tool）。
"""
import logging

from backend.agents.runtime.events import EventSink
from backend.agents.runtime.session import (
    APPROVAL_REQUEST,
    SANDBOX_ESCALATION,
    TOOL_PROGRESS,
    Session,
)
from backend.tool_system.events import APPROVAL_REQUEST as APPROVAL_REQUEST_EVENT
from backend.tool_system.events import SANDBOX_ESCALATION as SANDBOX_ESCALATION_EVENT
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
        elif event.type == APPROVAL_REQUEST_EVENT:
            # 待裁决事实入日志：UI 据此显示待批准卡片，冷恢复后据此清陈旧卡片
            self._session.append(APPROVAL_REQUEST, {
                "tool": event.tool_name,
                "tool_call_id": self._tool_call_id,
                **event.data,
            })
        elif event.type == SANDBOX_ESCALATION_EVENT:
            # 平铺 data（from / requested / to / granted / reason / justification）
            # ——与 tool/progress 同形状，前端轨迹按 tool_call_id 关联
            self._session.append(SANDBOX_ESCALATION, {
                "tool": event.tool_name,
                "tool_call_id": self._tool_call_id,
                **event.data,
            })
        if self._forward is not None:
            await self._forward(event.to_dict())
