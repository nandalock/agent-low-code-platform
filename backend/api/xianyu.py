from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.integrations.xianyu import manager

router = APIRouter(prefix="/api/xianyu", tags=["Xianyu"])


class ConnectRequest(BaseModel):
    cookie: str


@router.post("/connect")
async def connect_route(body: ConnectRequest):
    try:
        error = await manager.connect(body.cookie)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    if error:
        raise HTTPException(400, error)
    return {"ok": True}


@router.post("/disconnect")
async def disconnect_route():
    await manager.disconnect()
    return {"ok": True}


@router.get("/cookie")
def get_cookie_route() -> dict:
    return {"cookie": manager.get_saved_cookie()}


@router.get("/status")
def status_route() -> dict:
    return manager.get_status()


@router.get("/conversations")
def conversations_route() -> dict:
    return {"conversations": manager.get_conversations()}


@router.get("/conversations/{cid}")
def messages_route(cid: str) -> dict:
    return {"messages": manager.get_messages(cid)}


class FetchHistoryRequest(BaseModel):
    cid: str


@router.post("/conversations/fetch")
async def fetch_history_route(body: FetchHistoryRequest):
    try:
        count = await manager.fetch_history(body.cid)
        return {"ok": True, "count": count}
    except RuntimeError as e:
        raise HTTPException(400, str(e))
