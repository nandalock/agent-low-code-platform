import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.faq import router as faq_router
from backend.api.auth import router as auth_router
from backend.api.xianyu import router as xianyu_router
from backend.api.ws import router as ws_router
from backend.api.agents import router as agents_router

app = FastAPI(title="SaaS Customer Service")
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


@app.on_event("startup")
async def startup():
    from backend.db.init_db import init_db
    from backend.xianyu.manager import auto_connect
    from backend.agents import register
    from backend.agents.faqagent.agent import FaqAgent

    init_db()
    register(FaqAgent())

    asyncio.create_task(auto_connect())
