"""HumanHandoffAgent：转人工客服"""
import time

from backend.agents.base import BaseAgent, AgentReply
from backend.db.config_service import get_agent_definition, get_agent_config


class HumanHandoffAgent(BaseAgent):
    def __init__(self):
        definition = get_agent_definition("human_handoff") or {}
        self.key = "human_handoff"
        self.name = definition.get("name", "人工转接")
        self.desc = definition.get("description", "")
        self.status = "active"
        self.template = definition.get("config", {})

    def _get_config(self) -> dict:
        db_config = get_agent_config(self.key) or {}
        return {**self.template, **db_config}

    async def reply(self, tenant_id: int, question: str, context: dict | None = None) -> AgentReply:
        t0 = time.perf_counter()
        config = self._get_config()
        trace = {"total_ms": round((time.perf_counter() - t0) * 1000)}
        return AgentReply(answer=config["handoff_message"], tier="handoff", trace=trace)
