"""Agent 注册表"""
from backend.agents.base import BaseAgent

_agents: dict[str, BaseAgent] = {}


def register(agent: BaseAgent):
    _agents[agent.key] = agent


def list_agents() -> list[dict]:
    from backend.agents.router.router_runtime import RouterRuntime
    result = []
    for a in _agents.values():
        agent_type = "router" if isinstance(a, RouterRuntime) else "agent"
        cache_policy = getattr(a, '_definition', {}).get('cache_policy') if hasattr(a, '_definition') else None
        item = {
            "key": a.key, "name": a.name, "desc": a.desc,
            "status": a.status, "agent_type": agent_type,
            "cache_policy": cache_policy,
        }
        if isinstance(a, RouterRuntime):
            item["routable"] = _router_targets(a)
        result.append(item)
    return result


def _router_targets(router) -> list[str]:
    """路由器可能路由到的所有目标：routable_agents + L1 关键词目标 + fallback，去重保序"""
    from backend.agents.config_service import list_l1_keywords

    targets: list[str] = []

    def _add(k: str):
        if k and k not in targets:
            targets.append(k)

    cfg = router._cfg()  # 生效配置（definition + override 合并）
    for r in cfg.get("routable_agents", []):
        _add(r.get("key", ""))
    try:
        for rule in list_l1_keywords(router.key):
            _add(rule.get("target", ""))
    except Exception:
        pass
    _add(cfg.get("l3_llm", {}).get("fallback_agent", ""))
    return targets


def get_agent(key: str) -> BaseAgent:
    if key not in _agents:
        raise KeyError(f"Agent 不存在: {key}")
    return _agents[key]
