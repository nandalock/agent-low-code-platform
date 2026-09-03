"""Gateway — 系统请求入口边界（进程内服务，非 HTTP 层）

外部 Agent 驱动者（HTTP / Workflow / 渠道）→ Gateway → AgentRuntime。
Gateway 只做入口编排：身份/target 解析、会话映射、上下文装配、调 AgentRuntime、
结果/事件转发；不执行 Agent（无 LLM/Tool/MCP/AgentLoop），无 HTTP/SSE 语义。
"""
from backend.gateway.context import RequestContext
from backend.gateway.errors import (
    AgentBusy,
    AgentNotFound,
    GatewayError,
    InvalidGatewayContext,
    SessionNotFound,
)
from backend.gateway.service import AgentGateway

_gateway: AgentGateway | None = None


def get_gateway() -> AgentGateway:
    """进程内单例（Gateway 无状态：agent 从注册表现查，不缓存业务对象）"""
    global _gateway
    if _gateway is None:
        _gateway = AgentGateway()
    return _gateway


__all__ = [
    "AgentGateway",
    "RequestContext",
    "get_gateway",
    "GatewayError",
    "InvalidGatewayContext",
    "AgentNotFound",
    "SessionNotFound",
    "AgentBusy",
]
