"""Agent API"""
from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from backend.agents import list_agents, get_agent
from backend.agents.base import AgentReply
from backend.db.config_service import get_agent_config, save_agent_config

router = APIRouter(prefix="/api/agents", tags=["Agents"])


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]
    tier: str
    trace: dict = {}


class AgentConfigResponse(BaseModel):
    agent_key: str
    config: dict


@router.get("")
def agent_list() -> list[dict]:
    return list_agents()


@router.get("/{agent_key}/config")
def agent_get_config(agent_key: str) -> AgentConfigResponse:
    try:
        agent = get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    config = get_agent_config(agent_key) or agent.template
    return AgentConfigResponse(agent_key=agent_key, config=config)


@router.put("/{agent_key}/config")
def agent_save_config(agent_key: str, body: dict = Body(...)) -> AgentConfigResponse:
    try:
        get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    config = save_agent_config(agent_key, body)
    return AgentConfigResponse(agent_key=agent_key, config=config)


@router.post("/{agent_key}/chat")
async def agent_chat(
    agent_key: str,
    body: ChatRequest,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> ChatResponse:
    try:
        agent = get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    reply: AgentReply = await agent.reply(x_tenant_id, body.question)
    return ChatResponse(answer=reply.answer, sources=reply.sources, tier=reply.tier, trace=reply.trace)
