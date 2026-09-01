"""SessionPersistence — Session Event Log 持久化接口（DSH 思想）

职责划分（与 DSH 一致）：
  Session          负责单个会话的事件管理
  SessionStore     负责当前运行中的 Session 生命周期管理
  SessionPersistence 负责 Event Log 持久化（SessionHeader + SessionEvent[]）

只保存执行事实（SessionHeader + SessionEvent[]），不保存 OpenAI messages —
LLM 消息始终由 session.derive_messages() 派生，不做第二份存储。

Session / SessionStore 不感知具体存储；后续 PostgresPersistence 只需实现
本接口，不需要改动 Session / SessionStore / AgentLoop。
"""
import abc

from backend.agents.runtime.session.events import SessionEvent, SessionHeader


class SessionPersistence(abc.ABC):
    """Event Log 持久化接口。实现方保证 load 返回的事件按 seq 有序。"""

    @abc.abstractmethod
    def create(self, session_id: str, header: SessionHeader) -> None:
        """注册一个新 Session（写入 header）。Session 创建时调用一次。"""

    @abc.abstractmethod
    def append(self, session_id: str, event: SessionEvent) -> None:
        """追加一条事件。Session.append() 每次写入时调用。"""

    @abc.abstractmethod
    def load(self, session_id: str) -> tuple[SessionHeader, list[SessionEvent]] | None:
        """加载完整 Event Log；不存在返回 None。"""

    @abc.abstractmethod
    def list(self) -> list[str]:
        """列出已持久化的 session_id。"""


class InMemoryPersistence(SessionPersistence):
    """内存实现（第一版参考实现）：dict[session_id] -> {header, events}

    与 SessionStore 的内存 dict 不同：这里保存的是"可恢复的完整历史"，
    SessionStore 保存的是"正在运行"的 Session 实例。进程重启即丢失，
    后续以 PostgresPersistence 替代。
    """

    def __init__(self):
        self._data: dict[str, dict] = {}

    def create(self, session_id: str, header: SessionHeader) -> None:
        self._data[session_id] = {"header": header, "events": []}

    def append(self, session_id: str, event: SessionEvent) -> None:
        record = self._data.get(session_id)
        if record is None:
            raise KeyError(f"Session 未持久化: {session_id}")
        record["events"].append(event)

    def load(self, session_id: str) -> tuple[SessionHeader, list[SessionEvent]] | None:
        record = self._data.get(session_id)
        if record is None:
            return None
        return record["header"], list(record["events"])

    def list(self) -> list[str]:
        return list(self._data)
