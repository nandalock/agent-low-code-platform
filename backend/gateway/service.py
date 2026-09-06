"""AgentGateway — 外部 Agent 驱动者的统一进程内入口（DSH Request boundary）

调用链：
    HTTP (api/agents.py)  ─┐
    Workflow (engine/…)  ──┼──→  AgentGateway.chat()  →  AgentRuntime.reply()
    渠道 (integrations/…) ─┘

职责链：身份校验 → target 解析（注册表）→ 会话映射 → 上下文装配 →
       运行状态检查（预留）→ 调用 AgentRuntime → 返回结果 / 转发事件。

Gateway 本身不执行 Agent：禁止 LLM / Tool / MCP / AgentLoop 构造 / Prompt 构造 /
LangGraph 执行 —— 执行全部在已有的 AgentRuntime 内。Gateway 无状态：
agent 每次都从注册表现查，不缓存业务对象（DSH: resolve from registries, not cached）。

错误只抛 gateway/errors 定义的领域错误，通道层负责翻译。
"""
import logging
from collections.abc import Callable

from backend.agents.base import BaseAgent, AgentReply
from backend.agents.runtime.events import EventSink
from backend.agents.runtime.session import SessionEvent
from backend.gateway.context import RequestContext
from backend.gateway.errors import AgentNotFound, InvalidGatewayContext

logger = logging.getLogger(__name__)


class AgentGateway:
    """统一入口。所有方法只接收 RequestContext / 领域错误，不感知 HTTP/SSE/WS。"""

    def resolve(self, agent_key: str) -> BaseAgent:
        """target 解析：agent_key → 注册表中的 Agent 实例。

        KeyError 一律翻译为 AgentNotFound（Gateway 错误），不泄漏注册表异常。
        """
        if not agent_key:
            raise InvalidGatewayContext("agent_key 不能为空")
        from backend.agents import get_agent  # 延迟 import：gateway → agents 单向依赖
        try:
            return get_agent(agent_key)
        except KeyError:
            raise AgentNotFound(f"Agent 不存在: {agent_key}") from None

    async def chat(
        self,
        rctx: RequestContext,
        question: str,
        on_event: EventSink | None = None,
        session_event_sink: Callable[[SessionEvent], None] | None = None,
    ) -> AgentReply:
        """运行入口：身份校验 → 解析目标 → 调 AgentRuntime.reply()。

        参数形状与各 Agent.reply 的既有约定保持一致：
        context / session_id 所有已注册 Agent 都接受；
        on_event / session_event_sink 仅 AgentRuntime 系支持 —— 有 sink 才传入，
        行为与既有调用完全等价。Gateway 只做透传，不解释事件语义。
        """
        if not isinstance(rctx, RequestContext):
            raise InvalidGatewayContext("chat() 必须接收 RequestContext")
        if rctx.tenant_id is None:
            raise InvalidGatewayContext("RequestContext.tenant_id 缺失")

        agent = self.resolve(rctx.agent_key)

        if on_event is None and session_event_sink is None:
            return await agent.reply(
                rctx.tenant_id, question,
                context=rctx.context, session_id=rctx.session_id,
            )
        return await agent.reply(
            rctx.tenant_id, question,
            context=rctx.context, session_id=rctx.session_id,
            on_event=on_event,
            session_event_sink=session_event_sink,
        )
