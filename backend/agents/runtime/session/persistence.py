"""SessionPersistence — Session Event Log 持久化接口（DSH 思想，独立 capability seam）

当前阶段：Persistence 是独立接口占位，不参与 Session 的创建 / append 执行链。
Session 是进程内 Event-Sourced Session（Event Log 是唯一真源），运行态不落盘；
进程退出后 Session 消失是当前阶段允许的行为，不做磁盘恢复 / 跨进程恢复。

未来实现（独立 seam，无需改动 Session / SessionStore / AgentLoop）：
  JSONL / SQLite / PostgreSQL — 持久化保存的仍是 SessionHeader + SessionEvent[]，
  LLM messages 始终由 session.derive_messages() 派生，不存在单独的消息存储。
"""
import abc

from backend.agents.runtime.session.events import SessionEvent, SessionHeader


class SessionPersistence(abc.ABC):
    """Event Log 持久化接口。实现方保证 load 返回的事件按 seq 有序。"""

    @abc.abstractmethod
    def create(self, session_id: str, header: SessionHeader) -> None:
        """注册一个新 Session（写入 header）。"""

    @abc.abstractmethod
    def append(self, session_id: str, event: SessionEvent) -> None:
        """追加一条事件。"""

    @abc.abstractmethod
    def load(self, session_id: str) -> tuple[SessionHeader, list[SessionEvent]] | None:
        """加载完整 Event Log；不存在返回 None。"""

    @abc.abstractmethod
    def list(self) -> list[str]:
        """列出已持久化的 session_id。"""


class NoopPersistence(SessionPersistence):
    """空实现占位：所有操作 no-op。

    当前阶段不参与 Session 执行链，仅作接口示例 / 测试替身；
    不引入 JSONL / SQLite / Postgres 等任何存储假设。
    """

    def create(self, session_id: str, header: SessionHeader) -> None:
        pass

    def append(self, session_id: str, event: SessionEvent) -> None:
        pass

    def load(self, session_id: str) -> tuple[SessionHeader, list[SessionEvent]] | None:
        return None

    def list(self) -> list[str]:
        return []
