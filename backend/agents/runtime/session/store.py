"""SessionStore — 进程内运行态 Session 的生命周期管理(DSH 思想)

职责：create / get / put / list / delete 运行中的 Session 实例（dict[str, Session]）。
保存的是「正在运行的 Session 对象」（Event Log 在 Session 内部），不是数据库，
也不是消息历史。持久化是独立能力（SessionPersistence），SessionStore 不感知任何存储
（禁止出现 save_to_postgres 之类的职责）；热数据在内存，冷数据在 PostgreSQL。

取 Session 的三段式语义（恢复编排在 AgentRuntime，Store 只提供原语）：
  1. get(session_id) 命中        → 直接继续（进程内热 Session）
  2. get miss → Persistence.load → Session.from_events() replay → put() 登记（lazy restore）
  3. 完全不存在                  → create() 新建（调用方负责让该行为可观察）
启动时不加载全部历史 Session：按 session_id 按需恢复（Store = 热数据，DB = 持久数据）。

AgentRuntime 是无状态执行器，不持有 Session；进程内共享一个 SessionStore
（get_session_store()），所有 AgentRuntime 实例按 session_id 取回同一个 Session，
不同 session_id 对应完全隔离的 Session。

架构：
    SessionStore ──dict[str, Session]──> Session ──append──> Event Log ──> derive_messages ──> LLM
    SessionPersistence（独立）──append_events/flush──> PostgreSQL
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

        注意：只查内存热区。冷恢复（Persistence.load → from_events → put）由
        调用方（AgentRuntime）编排，Store 不直接依赖 Persistence。
        """
        return self._sessions.get(session_id)

    def put(self, session: Session) -> None:
        """登记一个已存在的 Session 实例（lazy restore 路径：从 Event Log replay
        出来的 Session 放入热区，之后 get 直接命中）。"""
        self._sessions[session.header.id] = session

    def list(self) -> list[str]:
        """当前运行中的 session_id 列表"""
        return list(self._sessions)

    def delete(self, session_id: str) -> bool:
        """从运行登记表移除（不再活跃）。Session 的 Event Log 是否删除 / 保留
        由 Persistence 决定（当前不删除历史），Store 只管运行态。"""
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        return True


_store = SessionStore()


def get_session_store() -> SessionStore:
    """进程级单例（与 get_registry() 同模式）：所有 AgentRuntime 共享同一 SessionStore"""
    return _store
