"""Session 模块（DSH 思想）— 执行事实 Event Log → Surface → derive_messages → LLM

职责划分（与 DeepSeek Harness 一致）：
  Session              单个会话的事件管理（SessionStore 创建，AgentLoop 消费）
  SessionStore         进程内运行态 Session 的生命周期管理（get_session_store() 单例）
  SessionPersistence   Event Log 持久化接口（独立 capability seam，当前阶段不参与执行链）
"""
from backend.agents.runtime.session.events import (
    ASSISTANT_CHUNK,
    ASSISTANT_MESSAGE,
    SEED,
    STEP_END,
    STEP_START,
    SURFACE_EVENT_TYPES,
    TOOL_CALL,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    USER_MESSAGE,
    SessionEvent,
    SessionHeader,
)
from backend.agents.runtime.session.persistence import (
    NoopPersistence,
    SessionPersistence,
)
from backend.agents.runtime.session.session import Session
from backend.agents.runtime.session.store import SessionStore, get_session_store
from backend.agents.runtime.session.surface import SurfaceManager

__all__ = [
    "Session",
    "SessionEvent",
    "SessionHeader",
    "SurfaceManager",
    "SessionStore",
    "get_session_store",
    "SessionPersistence",
    "NoopPersistence",
    "SEED",
    "TURN_START",
    "TURN_END",
    "STEP_START",
    "STEP_END",
    "USER_MESSAGE",
    "ASSISTANT_CHUNK",
    "ASSISTANT_MESSAGE",
    "TOOL_CALL",
    "TOOL_RESULT",
    "SURFACE_EVENT_TYPES",
]
