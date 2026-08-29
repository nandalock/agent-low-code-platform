import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.faq import router as faq_router
from backend.api.auth import router as auth_router
from backend.api.xianyu import router as xianyu_router
from backend.api.ws import router as ws_router
from backend.api.agents import router as agents_router
from backend.api.chat import router as chat_router
from backend.api.memory import router as memory_router
from backend.api.mcp import router as mcp_router
from backend.api.workflow import router as workflow_router

app = FastAPI(title="agent-low-code-platform")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3003"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(faq_router)
app.include_router(xianyu_router)
app.include_router(ws_router)
app.include_router(agents_router)
app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(mcp_router)
app.include_router(workflow_router)

@app.on_event("startup")
async def startup():
    from backend.core.schema import init_db
    from backend.agents import register, _agents
    from backend.agents.runtime import AgentRuntime
    from backend.agents.faqagent.agent import FaqAgent
    from backend.agents.supervisor.agent import SupervisorAgent
    from backend.agents.router import RouterAgent, RouterRuntime
    from backend.agents.human_handoff.agent import HumanHandoffAgent
    from backend.agents.config_service import list_agent_definitions
    from backend.mcp_service.registry import init_registry

    from backend.core.seeds import seed_orders

    init_db()
    seed_orders(tenant_id=1)

    # 1. 有自定义 logic 的 Agent
    register(FaqAgent())
    register(SupervisorAgent())
    register(RouterAgent())
    register(HumanHandoffAgent())

    # 2. 从 DB 自动发现剩余的纯 AgentRuntime agent
    for row in list_agent_definitions():
        if row["agent_key"] not in _agents:
            agent_type = row.get("agent_type", "agent")
            if agent_type == "router":
                register(RouterRuntime(key=row["agent_key"], definition=row))
            else:
                register(AgentRuntime(key=row["agent_key"], definition=row))

    # asyncio.create_task(auto_connect())  # 闲鱼已禁用

    # 先启动本地 MCP server (9001)，ToolRegistry 需要连接它
    from backend.mcp_service.server import mcp as mcp_server
    from starlette.middleware.cors import CORSMiddleware as StarletteCORS
    import uvicorn
    mcp_app = mcp_server.streamable_http_app()
    mcp_app.add_middleware(StarletteCORS, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["Mcp-Session-Id"])
    config = uvicorn.Config(mcp_app, host="0.0.0.0", port=9001, log_level="info")
    server_task = asyncio.create_task(uvicorn.Server(config).serve())
    await asyncio.sleep(0.5)  # 等 MCP server 就绪

    try:
        await init_registry()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"ToolRegistry 初始化失败（不影响服务启动）: {e}")
