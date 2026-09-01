"""Agent 基类"""
from dataclasses import dataclass, field


@dataclass
class AgentReply:
    answer: str
    sources: list[dict] = field(default_factory=list)
    tier: str = ""  # "direct" | "rag" | "fallback"
    trace: dict = field(default_factory=dict)
    session_id: str | None = None  # 多轮 Session id（AgentRuntime 返回；无 Session 的 Agent 为 None）


class BaseAgent:
    """所有 Agent 的抽象基类"""

    key: str = ""
    name: str = ""
    desc: str = ""
    status: str = "active"  # "active" | "draft"
    template: dict = {}

    async def reply(self, tenant_id: int, question: str, context: dict | None = None, session_id: str | None = None) -> AgentReply:
        raise NotImplementedError
