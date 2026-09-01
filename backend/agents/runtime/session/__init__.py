"""Session 模块（DSH 思想）— 执行事实 Event Log → Surface → derive_messages → LLM

对外暴露公共类型；AgentRuntime 创建 Session，AgentLoop 消费。
"""
from backend.agents.runtime.session.events import (
    ASSISTANT_CHUNK,
    ASSISTANT_MESSAGE,
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
from backend.agents.runtime.session.session import Session
from backend.agents.runtime.session.surface import SurfaceManager

__all__ = [
    "Session",
    "SessionEvent",
    "SessionHeader",
    "SurfaceManager",
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
