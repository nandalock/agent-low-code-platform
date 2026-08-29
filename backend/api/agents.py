"""Agent API"""
import json

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel

from backend.agents import list_agents, get_agent, register
from backend.agents.base import AgentReply
from backend.agents.runtime import AgentRuntime
from backend.agents.router.router_runtime import RouterRuntime, invalidate_desc_cache
from backend.agents.config_service import get_agent_config, save_agent_config, get_agent_definition
from backend.agents.config_service import list_l1_keywords, create_l1_keyword, update_l1_keyword, delete_l1_keyword
from backend.core.connection import get_conn
from backend.services.chat import service as chat_service

router = APIRouter(prefix="/api/agents", tags=["Agents"])


class ChatRequest(BaseModel):
    question: str
    conversation_id: int | None = None
    visitor_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]
    tier: str
    trace: dict = {}
    conversation_id: int


class AgentConfigResponse(BaseModel):
    agent_key: str
    config: dict


@router.get("")
def agent_list() -> list[dict]:
    return list_agents()


@router.post("")
def agent_create(body: dict = Body(...)) -> dict:
    key = (body.get("key") or "").strip()
    name = (body.get("name") or "").strip()
    desc = (body.get("desc") or "").strip()
    if not key or not name:
        raise HTTPException(400, "key 和 name 不能为空")

    # 检查是否已存在
    from backend.agents import _agents
    if key in _agents:
        raise HTTPException(409, f"Agent [{key}] 已存在")

    agent_type = body.get("agent_type", "agent")
    if agent_type not in ("agent", "router"):
        raise HTTPException(400, "agent_type 必须为 agent 或 router")

    if agent_type == "router":
        config = {
            "l1_enabled": True,
            "l2_embedding": {
                "enabled": True,
                "model": "bge-m3", "threshold": 0.85,
                "history_enabled": True, "description_enabled": True,
                "strategy": "cascade", "top_k": 3,
            },
            "l3_llm": {
                "enabled": True,
                "model": body.get("model", ""),
                "api_key": body.get("api_key", ""),
                "base_url": body.get("base_url", ""),
                "system_prompt_extra": "",
                "fallback_agent": "human_handoff",
            },
            "routable_agents": [],
        }
    else:
        config = {
            "system_prompt": body.get("system_prompt", ""),
            "fallback_reply": body.get("fallback_reply", "抱歉，我暂时无法处理。"),
            "max_steps": body.get("max_steps", 5),
            "api_key": body.get("api_key", ""),
            "base_url": body.get("base_url", ""),
            "model": body.get("model", ""),
        }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_definitions (agent_key, name, description, config, agent_type, cache_policy)
                   VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb)
                   ON CONFLICT (agent_key) DO UPDATE
                   SET name = EXCLUDED.name, description = EXCLUDED.description,
                       config = EXCLUDED.config, agent_type = EXCLUDED.agent_type,
                       cache_policy = COALESCE(agent_definitions.cache_policy, EXCLUDED.cache_policy)
                   RETURNING agent_key, name, description, config, status, agent_type""",
                (key, name, desc, json.dumps(config), agent_type, json.dumps(_default_cache_policy(key))),
            )
            row = dict(cur.fetchone())

    definition = {"agent_key": row["agent_key"], "name": row["name"], "description": row["description"],
                  "config": dict(row["config"]), "status": row["status"], "agent_type": row.get("agent_type", "agent"),
                  "cache_policy": row.get("cache_policy") or _default_cache_policy(key)}
    if agent_type == "router":
        register(RouterRuntime(key=key, definition=definition))
    else:
        register(AgentRuntime(key=key, definition=definition))
    return {"ok": True, "agent": {"key": key, "name": name, "desc": desc, "status": row["status"], "agent_type": agent_type}}


def _default_cache_policy(agent_key: str) -> dict:
    """所有 agent 统一的初始缓存策略，用户自行按需修改。"""
    return {
        "base_score": 0.50,
        "cacheable_intents": [],
        "block_entities": [
            {"name": "手机号", "pattern": r"\b1[3-9]\d{9}\b", "penalty": 0.0},
            {"name": "身份证", "pattern": r"\b\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "penalty": 0.0},
            {"name": "订单号", "pattern": r"\b\d{15,20}\b", "penalty": 0.0},
            {"name": "运单号", "pattern": r"\b\d{15,20}\b", "penalty": 0.0},
            {"name": "日期", "pattern": r"\b\d{4}-\d{2}-\d{2}\b", "penalty": 0.2},
            {"name": "金额", "pattern": r"\d+\.?\d*\s*元", "penalty": 0.2},
            {"name": "地址", "pattern": r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|重庆|天津|苏州|西安).{0,10}(区|路|街|楼|号)", "penalty": 0.3},
        ],
    }


@router.get("/{agent_key}/config")
def agent_get_config(agent_key: str) -> dict:
    try:
        get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    definition = get_agent_definition(agent_key) or {}
    base = definition.get("config", {})
    overrides = get_agent_config(agent_key) or {}
    config = {**base, **overrides}
    cache_policy = definition.get("cache_policy") or _default_cache_policy(agent_key)
    return {
        "agent_key": agent_key,
        "config": config,
        "cache_policy": cache_policy,
    }


@router.put("/{agent_key}/config")
def agent_save_config(agent_key: str, body: dict = Body(...)) -> dict:
    try:
        get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")

    # cache_policy 写入 agent_definitions，其余写入 agent_configs
    cache_policy = body.pop("cache_policy", None)
    config = save_agent_config(agent_key, body) if body else {}

    if cache_policy is not None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_definitions SET cache_policy = %s::jsonb, updated_at = now() WHERE agent_key = %s",
                    (json.dumps(cache_policy), agent_key),
                )
            conn.commit()

    invalidate_desc_cache(agent_key)
    return {"agent_key": agent_key, "config": config, "cache_policy": cache_policy}


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

    # 1. 获取或创建会话
    #    - 有 conversation_id → 继续当前会话（页面刷新）
    #    - 只有 visitor_id → 新建会话（新访问），visitor_id 仅做身份标识
    if body.conversation_id:
        conv = chat_service.get_conversation(x_tenant_id, body.conversation_id)
        if conv is None:
            raise HTTPException(404, "会话不存在")
    else:
        conv = chat_service.create_conversation(
            x_tenant_id,
            chat_service.ConversationCreate(
                channel=f"agent:{agent_key}",
                channel_conversation_id=body.visitor_id or None,
                customer_name="测试用户" if body.visitor_id else "匿名用户",
            ),
        )

    # 2. 写入用户消息
    chat_service.create_message(
        x_tenant_id, conv.id,
        chat_service.MessageCreate(role="customer", content=body.question),
    )

    # 3. 调用 Agent
    reply: AgentReply = await agent.reply(x_tenant_id, body.question)

    # 4. 写入 Agent 回复
    chat_service.create_message(
        x_tenant_id, conv.id,
        chat_service.MessageCreate(
            role="agent",
            sender_name=agent.name,
            content=reply.answer,
            metadata={"tier": reply.tier, "sources": reply.sources, "trace": reply.trace},
        ),
    )

    return ChatResponse(
        answer=reply.answer, sources=reply.sources, tier=reply.tier, trace=reply.trace,
        conversation_id=conv.id,
    )


@router.post("/{agent_key}/route-test")
async def agent_route_test(
    agent_key: str,
    body: ChatRequest,
    x_tenant_id: int = Header(default=1, alias="X-Tenant-ID"),
) -> dict:
    try:
        agent = get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    if not isinstance(agent, RouterRuntime):
        raise HTTPException(400, "仅 Router Agent 支持路由测试")

    reply = await agent.reply(x_tenant_id, body.question)
    try:
        routing_result = json.loads(reply.answer) if isinstance(reply.answer, str) else reply.answer
    except (json.JSONDecodeError, TypeError):
        routing_result = {"agent_key": reply.answer, "route_level": "?", "confidence": 0}
    return {
        "routing_result": routing_result,
        "trace": reply.trace,
    }


# ── L1 关键字 CRUD ──

class L1KeywordBody(BaseModel):
    keywords: list[str]
    target: str


class L1KeywordResponse(BaseModel):
    id: int
    router_key: str
    keywords: list[str]
    target: str
    created_at: str | None = None


@router.get("/{agent_key}/l1-keywords")
def agent_l1_list(agent_key: str) -> list[dict]:
    try:
        get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    return list_l1_keywords(agent_key)


@router.post("/{agent_key}/l1-keywords")
def agent_l1_create(agent_key: str, body: L1KeywordBody) -> dict:
    try:
        get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    if not body.keywords or not body.target:
        raise HTTPException(400, "keywords 和 target 不能为空")
    return create_l1_keyword(agent_key, body.keywords, body.target)


@router.put("/{agent_key}/l1-keywords/{rule_id}")
def agent_l1_update(agent_key: str, rule_id: int, body: L1KeywordBody) -> dict:
    try:
        get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    result = update_l1_keyword(rule_id, agent_key, body.keywords, body.target)
    if result is None:
        raise HTTPException(404, "规则不存在")
    return result


@router.delete("/{agent_key}/l1-keywords/{rule_id}")
def agent_l1_delete(agent_key: str, rule_id: int) -> dict:
    try:
        get_agent(agent_key)
    except KeyError:
        raise HTTPException(404, f"Agent 不存在: {agent_key}")
    ok = delete_l1_keyword(rule_id, agent_key)
    if not ok:
        raise HTTPException(404, "规则不存在")
    return {"ok": True}


# ── Ollama 模型列表 ──

@router.get("/ollama-models")
async def list_ollama_models() -> list[dict]:
    """读取 Ollama 本地已安装的 embedding 模型"""
    import os
    import aiohttp

    ollama_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ollama_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
    except Exception:
        return []

    models = []
    for m in data.get("models", []):
        caps = m.get("capabilities", []) if isinstance(m, dict) else []
        if "embedding" not in caps:
            continue
        detail = m.get("details", {}) or {}
        models.append({
            "name": m["name"],
            "size_mb": round(m.get("size", 0) / 1_000_000, 1),
            "dim": detail.get("embedding_length"),
            "quant": detail.get("quantization_level", ""),
        })
    return models
