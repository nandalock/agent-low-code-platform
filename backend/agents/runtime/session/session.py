"""Session — Agent 执行事实的 Event Log（DSH 思想）

职责：
  - 记录完整执行事件（log / seq / time 自动生成，log 外部只读）
  - 维护 ordered surface（LLM 可见子集）
  - 由 surface 派生 OpenAI 兼容 Message[]（derive_messages）
  - 同步广播 append 事件给 listener（UI projection / 持久化旁路）

Event Log 是唯一真源：会话创建时的初始消息（system prompt / 上游 context）
以 session/seed 事件写入 Log 开头（header.seed_length 记录数量），不做第二份存储；
LLM messages 始终由 derive_messages() 派生，不单独保存。

广播（listener）：append() 后同步通知已注册 listener（add_listener）。listener
是纯同步回调（Session.append 无 await 点，单进程事件循环内天然保序），用于
UI projection / 持久化等旁路消费；from_events 重建不回放 listener。
不负责：运行统计（trace）、Context Compaction、SubAgent Session、
Session 生命周期（SessionStore）、持久化（SessionPersistence）。
Session 不感知任何外部存储（JSONL / SQLite / Postgres）——Persistence 是独立
capability seam，当前阶段不参与执行链。
"""
import logging
import time
from collections.abc import Callable

from backend.agents.runtime.session.events import (
    SEED,
    SessionEvent,
    SessionHeader,
    new_session_id,
)
from backend.agents.runtime.session.surface import SurfaceManager

logger = logging.getLogger(__name__)


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
            seed_length=0,
        )
        self._log: list[SessionEvent] = []
        self._surface = SurfaceManager()
        self._listeners: list[Callable[[SessionEvent], None]] = []
        self._seq = 0
        # 初始 LLM 消息（system / 上游 context）→ seed 事件写入 Event Log：
        # LLM 可见（surface 事件）、随 Log 保存，Event Log 保持唯一真源
        if init_messages:
            self._header.seed_length = len(init_messages)
            for m in init_messages:
                self.append(SEED, {
                    "role": m.get("role", "system"),
                    "content": m.get("content", ""),
                })

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

    # ── listener（旁路广播）──

    def add_listener(self, cb: Callable[[SessionEvent], None]) -> None:
        """注册 append 广播 listener（同步回调，append 后按注册序调用）。

        单进程事件循环内 append 无 await 间隙，listener 收到的事件顺序与
        日志 seq 完全一致；listener 抛异常只记日志，不中断执行链。
        Session 会被 SessionStore 跨轮复用 → 调用方负责在轮末 remove_listener。
        """
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[SessionEvent], None]) -> None:
        """移除 listener（幂等：不存在时静默）"""
        if cb in self._listeners:
            self._listeners.remove(cb)

    @property
    def init_messages(self) -> list[dict]:
        """seed 事件派生（从 Event Log 读出，不做第二份存储）"""
        return [ev.data for ev in self._log if ev.type == SEED]

    @init_messages.setter
    def init_messages(self, value: list[dict]) -> None:
        """追加 seed 消息（已有 Session 补充初始上下文用）"""
        for m in value:
            self.append(SEED, {
                "role": m.get("role", "system"),
                "content": m.get("content", ""),
            })

    # ── 写入 ──

    def append(self, type: str, data: dict) -> SessionEvent:
        """追加一条执行事实：自增 seq → 记 time → 写 log → 通知 surface → 广播 listener。

        只有符合 Surface 规则的事件进入 SurfaceManager；不在这里处理
        Persistence（独立 capability seam）——listener 是旁路消费点
        （UI projection / 持久化桥），一个死掉的 listener 不能断掉执行链。
        """
        self._seq += 1
        event = SessionEvent(type=type, seq=self._seq, time=time.time(), data=data)
        self._log.append(event)
        self._surface.append(event)
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception:
                logger.exception(f"Session listener 异常（事件 {type} seq={event.seq}），已隔离")
        return event

    # ── 重建 ──

    @classmethod
    def from_events(
        cls,
        header: SessionHeader,
        events: list[SessionEvent],
    ) -> "Session":
        """从 SessionHeader + SessionEvent[] 重建 Session（纯内存，不涉及存储）。

        重建 _log / _surface / _seq（不回放 append()，避免重写 seq/time）。
        重建后 derive_messages() 结果与原 Session 完全一致（surface 由事件类型
        重建，seed 事件随 Log 一起恢复）。当前阶段无调用方——后续接入真正
        Persistence 时由 Persistence.load() → from_events() → SessionStore 使用。
        """
        session = cls(header=header)
        ordered = sorted(events, key=lambda e: e.seq)
        for ev in ordered:
            session._log.append(ev)
            session._surface.append(ev)
        session._seq = ordered[-1].seq if ordered else 0
        return session

    # ── 派生 LLM Message ──

    def derive_event_message(self, event: SessionEvent) -> dict | None:
        """单个 surface 事件 → OpenAI message；非 surface 事件返回 None"""
        t = event.type
        d = event.data
        if t == SEED:
            return {"role": d.get("role", "system"), "content": d.get("content", "")}
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
        """由 ordered surface 派生 LLM Message[]（seed 事件在前，surface 事件按序追加）"""
        messages: list[dict] = []
        for event in self._surface.events:
            msg = self.derive_event_message(event)
            if msg is not None:
                messages.append(msg)
        return messages
