"""SessionStore — 当前运行中 Session 的生命周期管理（DSH 思想）

职责：create / get / list / delete 运行中的 Session 实例。
不是数据库：持久化由 SessionPersistence 负责（Session 内部持有，随 append 写入）。

AgentRuntime 是无状态执行器，不持有 Session；进程内共享一个 SessionStore
（get_session_store()），每次请求按 session_id 取回同一个 Session。

架构：
    SessionStore ──持有──> SessionPersistence（注入给新建的 Session）
         │                        │
         └── create/get ──> Session ──append──> Event Log ──> 持久化
"""
from backend.agents.runtime.session.events import SessionEvent, SessionHeader
from backend.agents.runtime.session.persistence import InMemoryPersistence, SessionPersistence
from backend.agents.runtime.session.session import Session


class SessionStore:
    """运行中 Session 的内存登记表（第一版：dict[str, Session]）"""

    def __init__(self, persistence: SessionPersistence | None = None):
        self._sessions: dict[str, Session] = {}
        self._persistence = persistence

    def create(self, *, init_messages: list[dict] | None = None) -> Session:
        """创建新 Session 并登记。init_messages（system/上游 context）作为
        seed 事件写入 Event Log（LLM 可见），由 Session 内部转成事件。"""
        session = Session(init_messages=init_messages, persistence=self._persistence)
        self._sessions[session.header.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """取运行中的 Session；未运行则尝试从持久化恢复并重新登记。

        恢复依赖 Session.from_events()（header + Event Log 重建），
        进程重启后同一个 session_id 也能续上多轮对话。
        """
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        if self._persistence is not None:
            loaded = self._persistence.load(session_id)
            if loaded is not None:
                header, events = loaded
                session = Session.from_events(header, events, persistence=self._persistence)
                self._sessions[session_id] = session
        return session

    def list(self) -> list[str]:
        """当前运行中的 session_id 列表"""
        return list(self._sessions)

    def delete(self, session_id: str) -> bool:
        """从运行登记表移除（不再活跃）。历史 Event Log 仍在持久化层，
        再次 get() 会从持久化恢复 — 与 DSH 一致：Store 管运行态，Persistence 管历史。"""
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        return True


_store = SessionStore(persistence=InMemoryPersistence())


def get_session_store() -> SessionStore:
    """进程级单例（与 get_registry() 同模式）：所有 AgentRuntime 共享同一 SessionStore"""
    return _store
