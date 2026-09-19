"""Agent Runtime 包 — AgentRuntime（运行环境）+ AgentLoop（执行循环）+ events（事件定义）

对外保持兼容：`from backend.agents.runtime import AgentRuntime` 与重构前一致。
"""
# 顺序有讲究：loop 必须先于 session 进入 sys.modules ——
# session/tool_event_sink.py 要 loop.events 的 EventSink（见该文件头）。
from backend.agents.runtime.loop import AgentLoop
from backend.agents.runtime.loop.events import EventSink, TOOL_POLL_TIMEOUT
from backend.agents.runtime.agent_runtime import AgentRuntime
from backend.agents.runtime.session import Session

__all__ = ["AgentRuntime", "AgentLoop", "EventSink", "TOOL_POLL_TIMEOUT", "Session"]
