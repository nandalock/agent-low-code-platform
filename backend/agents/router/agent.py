"""Router Agent — 内置 RouterRuntime 实例（key="router"，fallback 读 supervisor 配置）"""
from backend.agents.router.router_runtime import RouterRuntime


class RouterAgent(RouterRuntime):
    def __init__(self):
        from backend.db.config_service import get_agent_definition
        definition = get_agent_definition("router") or get_agent_definition("supervisor") or {}
        super().__init__(key="router", definition=definition)
