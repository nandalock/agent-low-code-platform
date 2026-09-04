"""SessionPersistence — Session Event Log 持久化接口(DSH 思想,独立 capability seam)

职责边界(参考 DeepSeek Harness,不照搬实现):
  SessionStore     — 只负责「当前进程正在运行的 Session」(内存热区)
  SessionPersistence — 只负责 Event Log 的持久化;Event Log 是 Session 唯一事实来源,
                      恢复 = 读 Event Log → replay(Session.from_events),不做 Session JSON 快照

接口语义(同步模型,遵循项目 psycopg2 风格):
  create(session_id, header)   — 注册 Session 元信息(幂等 upsert)
  append_events(...)           — 事件进入 Persistence 待写缓冲(pending),不承诺落盘
  flush(session_id=None)       — 持久化边界:pending → durable(一次 flush = 一个事务,None=全部)
  load(session_id)             — 读 durable 完整 Event Log(冷恢复 replay 来源);不存在 → None
  exists(session_id)           — durable 存在性检查
  list()                       — 列出 durable session_id

append / flush 分开是刻意为之:Agent Event → SessionStore(Session) → append_events 收进
pending → flush 显式落库。turn 完成是 Persistence 的持久化边界(由 AgentRuntime 触发,
AgentLoop 不感知任何存储)。

NoopPersistence 保留为默认实现 / 测试替身;生产装配见 PostgresSessionPersistence
(postgres.py),由 main.py startup 注入,Session / SessionStore / AgentLoop 均不感知具体实现。
"""
import abc
import logging

from backend.agents.runtime.session.events import SessionEvent, SessionHeader

logger = logging.getLogger(__name__)


class SessionPersistence(abc.ABC):
    """Event Log 持久化接口。实现方保证 load 返回的事件按 seq 有序。"""

    @abc.abstractmethod
    def create(self, session_id: str, header: SessionHeader) -> None:
        """注册一个 Session 的元信息(幂等;重复 create 不覆盖已存在内容)。"""

    @abc.abstractmethod
    def append_events(self, session_id: str, events: list[SessionEvent]) -> None:
        """事件进入待写缓冲(pending),不承诺落盘;flush() 才是 durable 边界。

        实现方应自行过滤已持久化 / 已入 pending 的事件(seq 去重)。
        """

    @abc.abstractmethod
    def load(self, session_id: str) -> tuple[SessionHeader, list[SessionEvent]] | None:
        """读取 durable 的完整 Event Log(按 seq 有序);Session 不存在返回 None。

        冷恢复路径:load → Session.from_events(header, events) → SessionStore.put。
        注意:load 读的是 durable 状态(不含未 flush 的 pending)——同进程内 pending
        的 Session 必然同时存在于 SessionStore,不会走 load 路径。
        """

    @abc.abstractmethod
    def flush(self, session_id: str | None = None) -> None:
        """持久化边界:把 pending 事件落盘(session_id=None 时 flush 全部)。

        一次 flush 应是一个事务(全部成功或全部失败);失败时 pending 保留,可重放。
        """

    @abc.abstractmethod
    def exists(self, session_id: str) -> bool:
        """Session 是否已持久化(durable 检查,供恢复决策用)。"""

    @abc.abstractmethod
    def list(self) -> list[str]:
        """列出已持久化的 session_id。"""


class NoopPersistence(SessionPersistence):
    """空实现:所有操作 no-op(默认实现 / 测试替身)。

    不引入 JSONL / SQLite / Postgres 等任何存储假设;load 恒 None(视为不存在),
    flush 恒成功 —— 行为与「未装配持久化」等价。
    """

    def create(self, session_id: str, header: SessionHeader) -> None:
        pass

    def append_events(self, session_id: str, events: list[SessionEvent]) -> None:
        pass

    def load(self, session_id: str) -> tuple[SessionHeader, list[SessionEvent]] | None:
        return None

    def flush(self, session_id: str | None = None) -> None:
        pass

    def exists(self, session_id: str) -> bool:
        return False

    def list(self) -> list[str]:
        return []


# ── 进程级装配(与 get_session_store() 同模式) ──
# 默认 NoopPersistence:不装配时行为与改造前完全一致(进程内 Session,退出即消失)。
# main.py startup 调用 set_session_persistence(PostgresSessionPersistence()) 启用持久化;
# 测试可注入替身。

_persistence: SessionPersistence = NoopPersistence()


def get_session_persistence() -> SessionPersistence:
    """当前进程装配的 Persistence(默认 NoopPersistence)"""
    return _persistence


def set_session_persistence(p: SessionPersistence) -> None:
    """装配 Persistence 实现(进程级单例;启动时调用一次)"""
    global _persistence
    _persistence = p
    logger.info(f"SessionPersistence 已装配: {type(p).__name__}")
