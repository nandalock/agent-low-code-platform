"""Agent API"""
import asyncio
import json

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.agents import list_agents, get_agent, register
from backend.agents.base import AgentReply
from backend.agents.runtime import AgentRuntime
from backend.agents.runtime.session import Session, get_session_persistence, get_session_store
from backend.agents.runtime.session.events import SANDBOX_MODE
from backend.agents.runtime.session.sandbox_projection import project_sandbox_mode
from backend.agents.runtime.session.trajectory_projection import TrajectoryProjection, project_trajectory
from backend.agents.router.router_runtime import RouterRuntime, invalidate_desc_cache
from backend.agents.config_service import get_agent_config, save_agent_config, get_agent_definition
from backend.agents.config_service import list_l1_keywords, create_l1_keyword, update_l1_keyword, delete_l1_keyword
from backend.core.connection import get_conn
from backend.services.chat import service as chat_service
from backend.gateway import AgentNotFound, RequestContext, get_gateway

router = APIRouter(prefix="/api/agents", tags=["Agents"])


class ChatRequest(BaseModel):
    question: str
    conversation_id: int | None = None
    visitor_id: str | None = None
    session_id: str | None = None  # AgentRuntime 多轮 Session id：首轮不传（新建），后续传回（恢复）


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]
    tier: str
    trace: dict = {}
    conversation_id: int
    session_id: str | None = None


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


def _session_for_conversation(tenant_id: int, conversation_id: int | None, requested: str | None) -> str | None:
    """会话级 Session 恢复：显式 session_id（前端多轮回传）优先；
    否则查 conversations.session_id 映射（该 conversation 最近一次 chat 的 Session）——
    历史会话从任意入口继续（重启 / 换浏览器 / 切换会话）都能恢复 Agent 上下文。

    每次 chat 完成后本层把 reply.session_id 写回映射（save_conversation_session_id），
    保证 conversation ↔ session 关联持续存在。映射属 chat 域，Runtime 不感知 conversation。
    """
    if requested:
        return requested
    if conversation_id is None:
        return None
    return chat_service.get_conversation_session_id(tenant_id, conversation_id)


def _resolve_conversation(agent_key: str, body: ChatRequest, tenant_id: int):
    """获取或创建会话：有 conversation_id → 继续（页面刷新）；只有 visitor_id → 新建"""
    if body.conversation_id:
        conv = chat_service.get_conversation(tenant_id, body.conversation_id)
        if conv is None:
            raise HTTPException(404, "会话不存在")
    else:
        conv = chat_service.create_conversation(
            tenant_id,
            chat_service.ConversationCreate(
                channel=f"agent:{agent_key}",
                channel_conversation_id=body.visitor_id or None,
                customer_name="测试用户" if body.visitor_id else "匿名用户",
            ),
        )
    return conv


@router.post("/{agent_key}/chat")
async def agent_chat(
    agent_key: str,
    body: ChatRequest,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> ChatResponse:
    gateway = get_gateway()
    # 0. 入口存在性检查（与旧行为一致：Agent 不存在 → 404，且不创建会话/不落库）
    try:
        agent = gateway.resolve(agent_key)
    except AgentNotFound as e:
        raise HTTPException(404, str(e))

    # 1. 获取或创建会话（conversation 为 HTTP 通道的会话历史资源，属 chat 域，API 层持有）
    conv = _resolve_conversation(agent_key, body, x_tenant_id)

    # 2. 写入用户消息
    chat_service.create_message(
        x_tenant_id, conv.id,
        chat_service.MessageCreate(role="customer", content=body.question),
    )

    # 3. 统一经 Gateway 驱动 AgentRuntime（身份解析/会话映射/上下文装配在 Gateway 内；
    #    session_id 透传：多轮 Session 由 AgentRuntime + SessionStore 管理。
    #    无显式 session_id 时按 conversation 映射恢复（历史会话续聊带记忆）
    reply: AgentReply = await gateway.chat(
        RequestContext(
            tenant_id=x_tenant_id, agent_key=agent_key, channel="http",
            session_id=_session_for_conversation(x_tenant_id, conv.id, body.session_id),
            conversation_id=conv.id,
            visitor_id=body.visitor_id,
        ),
        body.question,
    )

    # 4. 写回 conversation → session 映射（本次 chat 使用的 Session，供历史会话冷恢复）
    if reply.session_id:
        chat_service.save_conversation_session_id(x_tenant_id, conv.id, reply.session_id)

    # 5. 写入 Agent 回复
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
        conversation_id=conv.id, session_id=reply.session_id,
    )


@router.post("/{agent_key}/chat/stream")
async def agent_chat_stream(
    agent_key: str,
    body: ChatRequest,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
):
    """SSE 流式聊天（Session Event → Trajectory Projection → UI events）。

    AgentLoop 执行事实写入 Session Event Log；本层经 session_event_sink 桥接
    TrajectoryProjection，把 typed events 投影为 UI 可直接渲染的 node 事件：
      {"type":"step","step":1,"total":8}        循环步进
      {"type":"traj/open","node":{...}}         think/tool/answer node 建立（全量字段）
      {"type":"traj/delta","id":"...","field":"thinking"|"text","delta":"..."}  增量
      {"type":"traj/update","id":"...","patch":{...}}  tool 状态/结果、answer 对账（全量值）
      {"type":"traj/close","id":"..."}          node 完成
      {"type":"usage","step":1,"prompt_tokens":...}  LLM usage（观测）
      {"type":"done","answer":"...","tier":"llm"} 结束（answer 为最终权威文本）
    session=None 遗留路径：AgentLoop 直接 on_event 推 legacy thinking/text/tool_* 事件。
    """
    gateway = get_gateway()
    # 0. 入口存在性检查（与旧行为一致：Agent 不存在 → 404，且不创建会话/不落库）
    try:
        agent = gateway.resolve(agent_key)
    except AgentNotFound as e:
        raise HTTPException(404, str(e))

    conv = _resolve_conversation(agent_key, body, x_tenant_id)
    chat_service.create_message(
        x_tenant_id, conv.id,
        chat_service.MessageCreate(role="customer", content=body.question),
    )

    # 统一经 Gateway 驱动 AgentRuntime；SSE 帧格式/事件翻译仍在本层（Gateway 不感知 HTTP）
    rctx = RequestContext(
        tenant_id=x_tenant_id, agent_key=agent_key, channel="http",
        session_id=_session_for_conversation(x_tenant_id, conv.id, body.session_id),
        conversation_id=conv.id,
        visitor_id=body.visitor_id,
    )

    async def event_gen():
        queue: asyncio.Queue = asyncio.Queue()
        thinking_parts: list[str] = []  # 累积思考过程，随消息落库持久化（旧数据/无轨迹回退兜底）
        projector = TrajectoryProjection()  # Session typed events → UI trajectory node 事件

        async def on_event(ev: dict):
            await queue.put(ev)

        # session 事件桥：Session.append 同步内联触发（无 await 间隙，与 log seq 严格保序）
        # → projector 投影 → 产物入队（put_nowait：同一事件循环内无背压风险）
        def on_session_event(ev):
            for uev in projector.handle(ev):
                queue.put_nowait(uev)

        task = asyncio.create_task(
            gateway.chat(rctx, body.question, on_event=on_event, session_event_sink=on_session_event),
        )
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    continue
                # thinking 累积双路径兼容：Session 开启 → traj/delta(field=thinking)；
                # session=None 遗留 → legacy thinking
                if ev.get("type") == "thinking":
                    thinking_parts.append(ev.get("delta", ""))
                elif ev.get("type") == "traj/delta" and ev.get("field") == "thinking":
                    thinking_parts.append(ev.get("delta", ""))
                # 会话 id 随 done 事件回传：前端据此恢复历史消息（conv_id 属于 API 层概念，
                # AgentRuntime 不感知 conversation — 分层保持 Runtime 无状态）
                if ev.get("type") == "done":
                    ev = {**ev, "conversation_id": conv.id}
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "done":
                    break
        finally:
            # 落库 agent 回复（done 事件后 reply 已完成），思考过程持久化到 metadata
            try:
                reply = task.result()
            except Exception:
                reply = None
            if reply is not None:
                # 写回 conversation → session 映射：SSE 轮次同样维护，历史会话冷恢复靠它
                if reply.session_id:
                    chat_service.save_conversation_session_id(x_tenant_id, conv.id, reply.session_id)
                thinking = "".join(thinking_parts)[:6000]  # 截断保护
                chat_service.create_message(
                    x_tenant_id, conv.id,
                    chat_service.MessageCreate(
                        role="agent",
                        sender_name=agent.name,
                        content=reply.answer,
                        metadata={
                            "tier": reply.tier, "sources": reply.sources, "trace": reply.trace,
                            "thinking": thinking or None,
                        },
                    ),
                )

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{agent_key}/route-test")
async def agent_route_test(
    agent_key: str,
    body: ChatRequest,
    x_tenant_id: int = Header(default=1, alias="X-Tenant-ID"),
) -> dict:
    gateway = get_gateway()
    try:
        agent = gateway.resolve(agent_key)
    except AgentNotFound as e:
        raise HTTPException(404, str(e))
    if not isinstance(agent, RouterRuntime):
        raise HTTPException(400, "仅 Router Agent 支持路由测试")

    reply = await gateway.chat(
        RequestContext(
            tenant_id=x_tenant_id, agent_key=agent_key, channel="http",
            visitor_id=body.visitor_id,
        ),
        body.question,
    )
    try:
        routing_result = json.loads(reply.answer) if isinstance(reply.answer, str) else reply.answer
    except (json.JSONDecodeError, TypeError):
        routing_result = {"agent_key": reply.answer, "route_level": "?", "confidence": 0}
    return {
        "routing_result": routing_result,
        "trace": reply.trace,
    }


def _session_or_404(session_id: str) -> Session:
    """取会话：SessionStore 热区优先，miss → Persistence.load 冷恢复并放回热区。"""
    store = get_session_store()
    session = store.get(session_id)
    if session is not None:
        return session
    loaded = get_session_persistence().load(session_id)
    if loaded is None:
        raise HTTPException(404, "session not found")
    header, events = loaded
    session = Session.from_events(header, events)
    store.put(session)
    return session


@router.get("/sessions/{session_id}/trajectory")
async def agent_session_trajectory(session_id: str) -> dict:
    """读取 Session 的 Agent 轨迹快照（Session Event → Conversation Projection）。

    会话的 Event Log（Postgres durable）经纯函数投影 → UI 可直接渲染的
    think/tool/answer node 序列 + usage。历史会话回放用：前端据 conversations.session_id
    恢复完整 Think/Tool 轨迹，而非仅有最终消息。
    读路径：SessionStore 热区优先，miss → Persistence.load（冷恢复）；两者皆无 → 404。
    """
    session = _session_or_404(session_id)
    snap = project_trajectory(session.events)
    return {"session_id": session_id, **snap}


class SandboxModeBody(BaseModel):
    mode: str


@router.get("/sessions/{session_id}/sandbox_mode")
async def agent_session_sandbox_mode(session_id: str) -> dict:
    """读取会话的沙箱模式：覆盖 + 部署默认 + 生效值。

    生效值 = 覆盖 ?? 部署默认 ?? workspace-write，与执行侧（sandbox/policy.py）
    是同一条优先级链；``override`` 来自 sandbox/mode 事件的投影（find-last）。
    """
    from backend.tool_system.sandbox.policy import FALLBACK_MODE
    from backend.tool_system.sandbox.runtime import default_mode
    events = _session_or_404(session_id).events
    override = project_sandbox_mode(events)
    cfg_default = default_mode()
    return {
        "session_id": session_id,
        "override": override,
        "default": cfg_default,
        "effective": override or cfg_default or FALLBACK_MODE,
    }


@router.post("/sessions/{session_id}/sandbox_mode")
async def agent_set_sandbox_mode(session_id: str, body: SandboxModeBody) -> dict:
    """写入会话级沙箱模式覆盖：追加一条 sandbox/mode 事件（日志即存储）。

    词汇封闭性在写入侧校验（非法值 400）。覆盖不持久化到任何配置存储——
    它就是日志里的一条事件，冷恢复后由投影重新折叠出同一值。
    """
    from backend.agents.runtime.agent_runtime import _flush_session_events
    from backend.tool_system.sandbox.policy import validate_mode
    try:
        mode = validate_mode(body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    session = _session_or_404(session_id)
    session.append(SANDBOX_MODE, {"mode": mode})
    _flush_session_events(get_session_persistence(), session)
    return {"session_id": session_id, "override": mode}


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
    from backend.core.http import get_http_session

    ollama_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    try:
        session = await get_http_session()
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
