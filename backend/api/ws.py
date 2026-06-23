import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.xianyu import manager

logger = logging.getLogger(__name__)

router = APIRouter()

_ws_clients: set[WebSocket] = set()


async def _broadcast(data: dict):
    dead: list[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


async def on_new_message(msg: dict):
    """manager 收到新消息时广播给所有前端 WS 客户端"""
    convs = manager.get_conversations()
    conv = next((c for c in convs if c["cid"] == msg["cid"]), None)
    await _broadcast({"type": "new_message", "data": msg, "conversation": conv})


manager.subscribe(on_new_message)


@router.websocket("/api/ws/chat")
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info("[WS] 客户端已连接")

    try:
        while True:
            await websocket.receive_text()  # 保持连接
    except WebSocketDisconnect:
        logger.info("[WS] 客户端已断开")
    finally:
        _ws_clients.discard(websocket)
