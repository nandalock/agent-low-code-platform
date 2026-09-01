"""Session — Agent 执行事实的 Event Log（DSH 思想）

职责：
  - 记录完整执行事件（log / seq / time 自动生成，log 外部只读）
  - 维护 ordered surface（LLM 可见子集）
  - 由 surface 派生 OpenAI 兼容 Message[]（derive_messages）

不负责：实时广播（EventSink）、运行统计（trace）、持久化（DB/Redis）、
Context Compaction、SubAgent Session。三者职责分离，Session 不替代任何一方。
"""
import time

from backend.agents.runtime.session.events import SessionEvent, SessionHeader, new_session_id
from backend.agents.runtime.session.surface import SurfaceManager


class Session:
    """执行事实的唯一来源。外部通过 append() 写入，log / surface 只读。"""

    def __init__(
        self,
        *,
        header: SessionHeader | None = None,
        init_messages: list[dict] | None = None,
    ):
        self._header = header or SessionHeader(
            version=1,
            id=new_session_id(),
            created_at=time.time(),
        )
        # 初始 LLM 消息（system / 上游 context），LLM 可见但不入 Event Log
        self._init_messages: list[dict] = list(init_messages or [])
        self._log: list[SessionEvent] = []
        self._surface = SurfaceManager()
        self._seq = 0

    # ── 只读视图 ──

    @property
    def header(self) -> SessionHeader:
        return self._header

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def log(self) -> list[SessionEvent]:
        """完整 Event Log（副本，外部不可修改）"""
        return list(self._log)

    @property
    def events(self) -> list[SessionEvent]:
        """完整事件流（同 log，语义名）"""
        return self.log

    @property
    def surface(self) -> SurfaceManager:
        return self._surface

    @property
    def init_messages(self) -> list[dict]:
        return list(self._init_messages)

    @init_messages.setter
    def init_messages(self, value: list[dict]) -> None:
        self._init_messages = list(value)

    # ── 写入 ──

    def append(self, type: str, data: dict) -> SessionEvent:
        """追加一条执行事实：自增 seq → 记 time → 写 log → 通知 surface"""
        self._seq += 1
        event = SessionEvent(type=type, seq=self._seq, time=time.time(), data=data)
        self._log.append(event)
        self._surface.append(event)
        return event

    # ── 派生 LLM Message ──

    def derive_event_message(self, event: SessionEvent) -> dict | None:
        """单个 surface 事件 → OpenAI message；非 surface 事件返回 None"""
        t = event.type
        d = event.data
        if t == "user/message":
            return {"role": "user", "content": d.get("content", "")}
        if t == "assistant/message":
            msg: dict = {
                "role": "assistant",
                "content": d.get("content") or "",
            }
            if d.get("tool_calls"):
                msg["tool_calls"] = d["tool_calls"]
            return msg
        if t == "tool/result":
            return {
                "role": "tool",
                "tool_call_id": d["tool_call_id"],
                "content": d.get("content", ""),
            }
        return None

    def derive_messages(self) -> list[dict]:
        """由 ordered surface 派生 LLM Message[]（init_messages 在前，surface 事件按序追加）"""
        messages = list(self._init_messages)
        for event in self._surface.events:
            msg = self.derive_event_message(event)
            if msg is not None:
                messages.append(msg)
        return messages
