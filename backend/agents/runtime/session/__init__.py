"""Session 模块（DSH 思想）— 执行事实 Event Log → Surface → derive_messages → LLM

**本文件是整个包对外的唯一界面**：外部一律 ``from backend.agents.runtime.session
import X``，不必知道 X 落在哪个子包里。子包内部怎么分、文件怎么挪，都不该让调用方
改 import —— 这正是把 16 个平铺文件归成 4 个子包时定下的规矩。

顶层留的是**核心**（事件定义、Session、Surface、Store），其余按用途分组：

  core（本层）           events.py · session.py · surface.py · store.py
  persistence/           落库 seam（接口 + Noop + 装配 + Postgres 实现）
  projections/           **只读**派生视图（沙箱模式 / trace / 轨迹）
  title/                 会话标题（净化 + fold + 服务）
  tests/                 自检脚本

职责划分（与 DeepSeek Harness 一致）：
  Session               单个会话的事件管理（SessionStore 创建，AgentLoop 消费）
  SessionStore          进程内运行态 Session 的生命周期管理（内存热区，不感知存储）
  SessionPersistence    Event Log 持久化接口（独立 capability seam）
  NoopPersistence       默认空实现（不装配时行为 = 改造前：进程退出 Session 消失）
  PostgresSessionPersistence  PostgreSQL 实现（session_headers + session_events，
                        由 main.py startup 装配；lazy restore 编排在 AgentRuntime）
  SessionTitleService   会话标题的接受与钉住（见 title/）

细节见 session.md 的「源码地图」。
"""
from backend.agents.runtime.session.events import (
    APPROVAL_REQUEST,
    ASSISTANT_CHUNK,
    ASSISTANT_MESSAGE,
    LLM_USAGE,
    SANDBOX_ESCALATION,
    SANDBOX_MODE,
    SEED,
    STEP_END,
    STEP_START,
    SURFACE_EVENT_TYPES,
    TITLE,
    TOOL_CALL,
    TOOL_PROGRESS,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    USER_MESSAGE,
    SessionEvent,
    SessionHeader,
)
from backend.agents.runtime.session.persistence import (
    NoopPersistence,
    PostgresSessionPersistence,
    SessionPersistence,
    flush_session_events,
    get_session_persistence,
    set_session_persistence,
)
from backend.agents.runtime.session.session import Session
from backend.agents.runtime.session.store import SessionStore, get_session_store
from backend.agents.runtime.session.surface import SurfaceManager
from backend.agents.runtime.session.title import (
    FALLBACK_MAX_BYTES,
    FALLBACK_MAX_WORDS,
    MAX_TITLE_BYTES,
    SOURCE_FALLBACK,
    SOURCE_PROVIDER,
    SOURCE_USER,
    SessionTitle,
    SessionTitleService,
    TitleInput,
    TitleMessage,
    collect_title_messages,
    fallback_session_title,
    fold_session_title,
    fold_title_input,
    get_session_title_service,
    normalize_session_title,
    truncate_title_utf8,
)

__all__ = [
    # ── 核心 ──
    "Session",
    "SessionEvent",
    "SessionHeader",
    "SurfaceManager",
    "SessionStore",
    "get_session_store",
    # ── 持久化 seam ──
    "SessionPersistence",
    "NoopPersistence",
    "PostgresSessionPersistence",
    "get_session_persistence",
    "set_session_persistence",
    "flush_session_events",
    # ── 会话标题 ──
    "SessionTitleService",
    "get_session_title_service",
    "SessionTitle",
    "TitleInput",
    "TitleMessage",
    "fold_session_title",
    "fold_title_input",
    "collect_title_messages",
    "SOURCE_FALLBACK",
    "SOURCE_PROVIDER",
    "SOURCE_USER",
    "fallback_session_title",
    "normalize_session_title",
    "truncate_title_utf8",
    "FALLBACK_MAX_WORDS",
    "FALLBACK_MAX_BYTES",
    "MAX_TITLE_BYTES",
    # ── 事件类型常量 ──
    "SEED",
    "TURN_START",
    "TURN_END",
    "STEP_START",
    "STEP_END",
    "USER_MESSAGE",
    "ASSISTANT_CHUNK",
    "ASSISTANT_MESSAGE",
    "LLM_USAGE",
    "APPROVAL_REQUEST",
    "SANDBOX_ESCALATION",
    "SANDBOX_MODE",
    "TITLE",
    "TOOL_CALL",
    "TOOL_PROGRESS",
    "TOOL_RESULT",
    "SURFACE_EVENT_TYPES",
]
