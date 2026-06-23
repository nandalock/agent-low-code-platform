"""Agent 注册表"""
from backend.agents.base import BaseAgent

_agents: dict[str, BaseAgent] = {}


def register(agent: BaseAgent):
    _agents[agent.key] = agent


def list_agents() -> list[dict]:
    return [
        {"key": a.key, "name": a.name, "desc": a.desc, "status": a.status}
        for a in _agents.values()
    ]


def get_agent(key: str) -> BaseAgent:
    if key not in _agents:
        raise KeyError(f"Agent 不存在: {key}")
    return _agents[key]
