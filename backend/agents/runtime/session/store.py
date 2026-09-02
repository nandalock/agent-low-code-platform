"""SessionStore — 进程内运行态 Session 的生命周期管理（DSH 思想）

职责：create / get / list / delete 运行中的 Session 实例（dict[str, Session]）。
保存的是「正在运行的 Session 对象」（Event Log 在 Session 内部），不是数据库，
也不是消息历史。持久化是独立能力（SessionPersistence），当前阶段不参与：
进程退出后 Session 消失是允许的行为，不做磁盘恢复 / 跨进程恢复。

AgentRuntime 是无状态执行器，不持有 Session；进程内共享一个 SessionStore
（get_session_store()），所有 AgentRuntime 实例按 session_id 取回同一个 Session，
不同 session_id 对应完全隔离的 Session。

架构：
    SessionStore ──dict[str, Session]──> Session ──append──> Event Log ──> derive_messages ──> LLM
"""
from backend.agents.runtime.session.session import Session


class SessionStore:
    """运行中 Session 的内存登记表（dict[str, Session]）"""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(self, *, init_messages: list[dict] | None = None) -> Session:
        """创建新 Session 并登记。init_messages（system/上游 context）作为
        seed 事件写入 Event Log（LLM 可见），由 Session 内部转成事件。"""
        session = Session(init_messages=init_messages)
        self._sessions[session.header.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """取运行中的 Session；不存在返回 None。

        当前阶段不实现持久化恢复：进程内找不到即视为新会话（由调用方 create）。
        """
        return self._sessions.get(session_id)

    def list(self) -> list[str]:
        """当前运行中的 session_id 列表"""
        return list(self._sessions)

    def delete(self, session_id: str) -> bool:
        """从运行登记表移除（不再活跃）。历史 Event Log 不做持久化，
        删除后无法恢复 — 与 DSH 当前阶段一致：Store 只管运行态。"""
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        return True


_store = SessionStore()


def get_session_store() -> SessionStore:
    """进程级单例（与 get_registry() 同模式）：所有 AgentRuntime 共享同一 SessionStore"""
    return _store
