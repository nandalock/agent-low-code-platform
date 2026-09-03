"""Gateway 领域错误 — 与 HTTP/SSE/WebSocket 无关

由通道层（API adapter）翻译成通道错误，Gateway 不直接产生 HTTPException。
对齐 DSH：Gateway 错误携带稳定 code（如 session/agent-busy），协议层负责映射。

AgentBusy / SessionNotFound 为后续阶段（run ownership / 冷会话恢复）预留的词汇，
当前阶段不抛；InvalidGatewayContext / AgentNotFound 本阶段已使用。
"""
from typing import Optional


class GatewayError(Exception):
    code = "gateway/error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class InvalidGatewayContext(GatewayError):
    """RequestContext 缺失运行必需字段（tenant_id / agent_key 为空等）"""
    code = "gateway/invalid-context"


class AgentNotFound(GatewayError):
    """目标 agent 不在注册表中（key 不存在）"""
    code = "gateway/agent-not-found"


class SessionNotFound(GatewayError):
    """session_id 对应 Session 不存在（预留：冷会话恢复策略启用后使用）"""
    code = "session/not-found"


class AgentBusy(GatewayError):
    """同一 Session 正被另一个 run 驱动（预留：run ownership 启用后使用）"""
    code = "session/agent-busy"
