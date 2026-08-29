"""Supervisor 编排 Agent — LangGraph 三级意图路由

路由策略:
  L1 关键词匹配 (<1ms) — 60-80% 请求
  L2 向量语义 (30-100ms) — 15-25% 请求  [占位]
  L3 大模型 FC (1-2s) — 5-15% 请求

子 Agent:
  faqagent ✓ / order_agent 占位 / ticket_agent 占位 / human_handoff 占位
"""
import logging
import re
import json
import time
from typing import Annotated, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

from backend.agents.base import BaseAgent, AgentReply
from backend.agents.config_service import get_agent_definition, get_agent_config
from backend.services.memory.hooks import MemoryHook

logger = logging.getLogger(__name__)


# ── L3 Function Calling 工具定义 ──

L3_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "route_to_faq",
            "description": "用户咨询产品知识、公司政策、退款规则、操作流程等可用知识库回答的问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用户的核心问题"},
                    "topic": {"type": "string", "description": "问题主题：产品/政策/流程/员工服务"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_to_order",
            "description": "用户查询订单状态、物流、催单、修改订单备注等售中问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户的具体需求"},
                    "order_id": {"type": "string", "description": "订单号（用户提了才填）"},
                    "action": {"type": "string", "enum": ["query", "urge", "modify", "cancel"], "description": "操作类型"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_to_ticket",
            "description": "用户需要投诉、维修、换货、创建工单或查询工单进度等售后问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "投诉/工单原因"},
                    "action": {"type": "string", "enum": ["create", "query", "urge"], "description": "工单操作"},
                    "priority": {"type": "string", "enum": ["normal", "urgent"], "description": "紧急程度"},
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_to_human",
            "description": "用户明确要求转人工、机器人无法处理、或用户情绪激动需要人工介入",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "转人工原因"},
                    "priority": {"type": "string", "enum": ["normal", "urgent"], "description": "紧急程度"},
                },
                "required": ["reason"],
            },
        },
    },
]

# tool name → agent key 映射
_TOOL_AGENT_MAP = {
    "route_to_faq": "faqagent",
    "route_to_order": "order_agent",
    "route_to_ticket": "ticket_agent",
    "route_to_human": "human_handoff",
}

L3_SYSTEM_PROMPT = """你是一个客服意图路由系统。分析用户消息，选择一个最合适的路由。

规则：
1. 必须调用一个 function，不要直接回复文本
2. 知识库类问题（怎么/如何/是什么/退款政策/公司制度）→ route_to_faq
3. 订单相关（查物流/催单/改备注/取消）→ route_to_order
4. 投诉售后（投诉/维修/换货/工单进度）→ route_to_ticket
5. 明确要求转人工、情绪激动、或者以上都无法处理 → route_to_human
6. 不确定 → route_to_faq（兜底）

{memory_context}"""


# ── State ──

class AgentState(dict):
    """Supervisor 编排状态"""
    messages: Annotated[list[BaseMessage], add_messages]
    tenant_id: int
    session_id: str
    context: str              # 记忆系统组装的上下文（子 Agent 共用）
    intent: str
    slots: dict
    sub_results: dict
    final_response: str
    route_tier: str  # "L1" | "L2" | "L3"
    # 记忆系统内部状态（由 MemoryContext.load/save 管理）
    _mem_processed: int
    memory: dict  # PromptContext.to_dict()


# ── L1 关键词路由 ──

class KeywordRouter:
    """L1 关键词匹配 — 纯字符串扫描，<1ms"""

    def __init__(self, rules: dict[str, list[str]]):
        self._patterns: dict[str, list[re.Pattern]] = {}
        for intent, keywords in rules.items():
            self._patterns[intent] = [re.compile(re.escape(kw)) for kw in keywords]

    def match(self, text: str) -> str | None:
        for intent, patterns in self._patterns.items():
            for pat in patterns:
                if pat.search(text):
                    return intent
        return None


# ── Supervisor 节点 ──

class SupervisorNode:
    """Supervisor 编排节点"""

    def __init__(self, memory: MemoryHook | None = None):
        definition = get_agent_definition("supervisor") or {}
        base = definition.get("config", {})
        self.config = {**base, **(get_agent_config("supervisor") or {})}
        self.keywords = KeywordRouter(self.config.get("l1_keywords", {}))
        self.memory = memory or MemoryHook()
        self._llm: ChatOpenAI | None = None

    @property
    def llm(self) -> ChatOpenAI:
        if self._llm is None:
            self._llm = ChatOpenAI(
                model=self.config.get("model") or "deepseek-chat",
                api_key=self.config.get("api_key") or "sk-xxx",
                base_url=self.config.get("base_url") or "https://api.deepseek.com",
                temperature=0,
            )
        return self._llm

    # ── 路由 ──

    async def route(self, state: AgentState) -> AgentState:
        """三级意图路由"""
        messages = state.get("messages", [])
        if not messages:
            return {**state, "intent": "faqagent", "route_tier": "L1"}

        last_msg = messages[-1]
        question = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)

        # 记忆系统：只传本轮新增消息
        self.memory.load_state(state)
        new_msgs = messages[self.memory.buffer_len:]  # 增量
        ctx = await self.memory.on_assemble(
            messages=new_msgs, llm=self.llm,
            system_prompt=L3_SYSTEM_PROMPT,
            tenant_id=state.get("tenant_id", 0),
            user_id=state.get("session_id", ""),
        )
        mem_state = self.memory.save_state()
        mem_state["memory"] = ctx.to_dict()  # PromptContext 存入 state

        # 路由上下文：用内部缓冲取滑动窗口
        route_ctx = self.memory.quick_context(self.memory._buffer)

        # L1: 关键词
        intent = self.keywords.match(question)
        if intent:
            logger.info(f"[L1] 关键词命中: {intent}")
            return {**state, **mem_state, "context": route_ctx.recent,
                    "intent": intent, "route_tier": "L1", "slots": {}}

        # L3: 大模型 FC
        intent, slots = await self._l3_llm_fc(question, state)
        logger.info(f"[L3] LLM 路由: {intent} slots={slots}")
        return {**state, **mem_state, "context": route_ctx.recent,
                "intent": intent, "route_tier": "L3", "slots": slots}

    # ── L3 大模型 Function Calling ──

    async def _l3_llm_fc(self, question: str, state: AgentState) -> tuple[str, dict]:
        """大模型 FC 一次调用完成意图识别 + 槽位提取"""
        try:
            t0 = time.perf_counter()

            # 占位符填充：记忆系统上下文 → 替换 prompt 中的 {memory_context}
            context = state.get("context", "")
            prompt = L3_SYSTEM_PROMPT.format(
                memory_context=f"## 对话上下文\n{context}" if context else ""
            )

            response = await self.llm.ainvoke(
                [
                    SystemMessage(content=prompt),
                    HumanMessage(content=question),
                ],
                tools=L3_TOOLS,
                tool_choice="required",
            )

            tool_calls = getattr(response, 'tool_calls', None) or []
            if not tool_calls:
                raise ValueError("LLM 未返回 tool_call")

            tc = tool_calls[0]
            intent = _TOOL_AGENT_MAP.get(tc["name"], "faqagent")
            slots = tc.get("args", {})
            ms = round((time.perf_counter() - t0) * 1000)
            logger.info(f"[L3] FC 完成: intent={intent} ms={ms}")
            return intent, slots

        except Exception as e:
            logger.warning(f"[L3] FC 失败: {e}")
            return "human_handoff", {"reason": f"路由失败: {e}"}

    # ── 汇总 ──

    async def synthesize(self, state: AgentState) -> AgentState:
        """汇总子 Agent 结果 + 异步持久化"""
        sub = state.get("sub_results", {})
        parts = [v for v in sub.values() if v]
        final = "\n\n".join(parts) if parts else self.config.get("fallback_reply", "无法处理")

        # 记忆接入点：会话结束后异步持久化（提取画像 + 写 user_profiles）
        await self.memory.on_persist(
            messages=state.get("messages", []),
            tenant_id=state.get("tenant_id", 0),
            user_id=state.get("session_id", ""),
        )

        return {
            **state,
            "final_response": final,
            "messages": [AIMessage(content=final)],
        }


# ── 子 Agent 节点 ──

async def _call_real_agent(agent_key: str, state: AgentState) -> str:
    """调用已注册的 Agent，传入完整的记忆上下文"""
    from backend.agents import get_agent
    try:
        agent = get_agent(agent_key)
        msg = state["messages"]
        question = msg[-1].content if msg and hasattr(msg[-1], 'content') else ""

        # 子 Agent：只传本轮新增
        _supervisor_node.memory.load_state(state)
        new_msgs = msg[_supervisor_node.memory.buffer_len:]
        ctx = await _supervisor_node.memory.on_assemble(
            messages=new_msgs, llm=_supervisor_node.llm,
            tenant_id=state.get("tenant_id", 0),
            user_id=state.get("session_id", ""),
        )
        full_question = f"{ctx.full}\n\n用户：{question}" if ctx.full else question
        reply: AgentReply = await agent.reply(state.get("tenant_id", 1), full_question)
        return reply.answer
    except KeyError:
        return f"[{agent_key}] Agent 未注册"
    except Exception as e:
        return f"[{agent_key}] 调用失败: {e}"



async def faqagent_node(state: AgentState) -> AgentState:
    answer = await _call_real_agent("faqagent", state)
    return {**state, "sub_results": {**state.get("sub_results", {}), "faqagent": answer}}


async def ticket_agent_node(state: AgentState) -> AgentState:
    answer = await _call_real_agent("ticket_agent", state)
    return {**state, "sub_results": {**state.get("sub_results", {}), "ticket_agent": answer}}


async def order_agent_node(state: AgentState) -> AgentState:
    answer = await _call_real_agent("order_agent", state)
    return {**state, "sub_results": {**state.get("sub_results", {}), "order_agent": answer}}


async def human_handoff_node(state: AgentState) -> AgentState:
    answer = await _call_real_agent("human_handoff", state)
    return {**state, "sub_results": {**state.get("sub_results", {}), "human_handoff": answer}}


# ── 路由映射 ──

ROUTE_MAP = {
    "faqagent": "faqagent",
    "ticket_agent": "ticket_agent",
    "order_agent": "order_agent",
    "human_handoff": "human_handoff",
}


def route_after_supervisor(state: AgentState) -> str:
    return ROUTE_MAP.get(state.get("intent", ""), "faqagent")


# ── 构建 Graph ──

_checkpointer = MemorySaver()
_supervisor_node: SupervisorNode | None = None


def build_graph() -> StateGraph:
    """构建 graph 结构（节点 + 边），graph 只编译一次"""
    global _supervisor_node
    if _supervisor_node is None:
        _supervisor_node = SupervisorNode()

    node = _supervisor_node
    graph = StateGraph(AgentState)

    graph.add_node("supervisor_route", node.route)
    graph.add_node("faqagent", faqagent_node)
    graph.add_node("ticket_agent", ticket_agent_node)
    graph.add_node("order_agent", order_agent_node)
    graph.add_node("human_handoff", human_handoff_node)
    graph.add_node("synthesize", node.synthesize)

    graph.set_entry_point("supervisor_route")
    graph.add_conditional_edges("supervisor_route", route_after_supervisor, ROUTE_MAP)
    for agent_key in ROUTE_MAP:
        graph.add_edge(agent_key, "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile(checkpointer=_checkpointer)


# ── Agent 注册 ──

class SupervisorAgent(BaseAgent):
    def __init__(self):
        definition = get_agent_definition("supervisor") or {}
        self.key = "supervisor"
        self.name = definition.get("name", "Supervisor")
        self.desc = definition.get("description", "")
        self.status = "active"
        self.template = definition.get("config", {})

    async def reply(self, tenant_id: int, question: str, context: dict | None = None) -> AgentReply:
        from backend.services.memory import MemoryContext

        graph = build_graph()
        # 每次请求注入独立的记忆实例
        _supervisor_node.memory = MemoryContext()

        state = {
            "messages": [HumanMessage(content=question)],
            "tenant_id": tenant_id,
            "session_id": str(tenant_id),
            "context": json.dumps(context, ensure_ascii=False) if context else "",
            "intent": "",
            "slots": {},
            "sub_results": {},
            "final_response": "",
            "route_tier": "",
            # _mem_buffer / _mem_summary 由 checkpointer 恢复，不覆盖
        }
        config = {"configurable": {"thread_id": f"tenant_{tenant_id}"}}
        result = await graph.ainvoke(state, config=config)

        return AgentReply(
            answer=result.get("final_response", "系统异常"),
            tier="supervisor",
            trace={
                "intent": result.get("intent", "unknown"),
                "route_tier": result.get("route_tier", ""),
            },
        )
