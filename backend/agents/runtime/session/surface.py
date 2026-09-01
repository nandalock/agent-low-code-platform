"""SurfaceManager — 决定哪些 SessionEvent 对 LLM 可见（ordered surface）

Event Log 是完整执行事实；Surface 是 LLM 可见子集。
规则：只有 user/message、assistant/message、tool/result 进入 surface，
其余（turn/start、step/start、assistant/chunk、tool/call、step/end、turn/end）
只存在于 Event Log，由 derive_messages 过滤掉。
"""
from backend.agents.runtime.session.events import SURFACE_EVENT_TYPES, SessionEvent


class SurfaceManager:
    def __init__(self):
        self._events: list[SessionEvent] = []

    def append(self, event: SessionEvent) -> None:
        """只有 surface 事件进入 LLM 可见列表，保持 append 顺序"""
        if event.type in SURFACE_EVENT_TYPES:
            self._events.append(event)

    @property
    def events(self) -> list[SessionEvent]:
        """有序 surface 事件（返回副本，外部只读）"""
        return list(self._events)
