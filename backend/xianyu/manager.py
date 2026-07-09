"""闲鱼连接管理器：单例管理 XianyuLive 的启停 + 数据库持久化"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.xianyu.live import XianyuLive
from backend.chat import service as chat_service

logger = logging.getLogger(__name__)

# Cookie 持久化文件（在项目根目录）
if os.path.exists("/app"):
    _COOKIE_FILE = Path("/app/xianyu_cookie.json")
else:
    _COOKIE_FILE = Path(__file__).parent.parent.parent / "xianyu_cookie.json"

DEFAULT_TENANT_ID = int(os.getenv("XIANYU_TENANT_ID", "1"))

# 连接状态
_live: XianyuLive | None = None
_task: asyncio.Task | None = None
_connected_at: datetime | None = None

# 前端 WebSocket 订阅者列表
_subscribers: list = []


def subscribe(callback):
    _subscribers.append(callback)


def unsubscribe(callback):
    try:
        _subscribers.remove(callback)
    except ValueError:
        pass


def _h5tk_from_cookie(cookie: str) -> str:
    for p in cookie.split("; "):
        if p.startswith("_m_h5_tk="):
            return p.split("=", 1)[1].split("_")[0]
    return ""


def _cookie_changed(new_cookie: str) -> bool:
    old = get_saved_cookie()
    return bool(old) and _h5tk_from_cookie(old) != _h5tk_from_cookie(new_cookie)


def get_saved_cookie() -> str:
    try:
        if _COOKIE_FILE.exists():
            data = json.loads(_COOKIE_FILE.read_text("utf-8"))
            return data.get("cookie", "")
    except Exception:
        pass
    return ""


def get_cached_token() -> tuple[str, str]:
    try:
        if _COOKIE_FILE.exists():
            data = json.loads(_COOKIE_FILE.read_text("utf-8"))
            return data.get("token", ""), data.get("device_id", "")
    except Exception:
        pass
    return "", ""


def save_cookie_to_file(cookie: str, token: str = "", device_id: str = ""):
    _COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if _COOKIE_FILE.exists():
        try:
            existing = json.loads(_COOKIE_FILE.read_text("utf-8"))
        except Exception:
            pass
    existing["cookie"] = cookie
    if token:
        existing["token"] = token
    if device_id:
        existing["device_id"] = device_id
    _COOKIE_FILE.write_text(json.dumps(existing, ensure_ascii=False), "utf-8")


async def _on_message(msg: dict):
    """收到新消息，写入数据库，并通知前端 WebSocket 订阅者"""
    cid = msg["cid"]
    if not cid:
        return

    now = datetime.now(timezone.utc).isoformat()
    msg["time"] = now

    try:
        conv = chat_service.get_or_create_conversation(
            DEFAULT_TENANT_ID, "xianyu", cid,
            customer_name=msg.get("sender_name", ""),
            customer_id=msg.get("sender_id", ""),
        )
        chat_service.create_message(
            DEFAULT_TENANT_ID, conv.id,
            chat_service.MessageCreate(
                role="customer",
                sender_name=msg.get("sender_name", ""),
                content=msg.get("content", ""),
            ),
        )
    except Exception as e:
        logger.error(f"写入消息到数据库失败: {e}")

    logger.info(f"[闲鱼消息] cid={cid} {msg['sender_name']}: {msg['content'][:80]}")

    for cb in _subscribers:
        try:
            await cb(msg)
        except Exception:
            pass


def get_status() -> dict:
    return {
        "is_online": _live is not None and _task is not None and not _task.done(),
        "connected_at": _connected_at.isoformat() if _connected_at else None,
    }


def get_conversations() -> list[dict]:
    """返回会话列表（从数据库读取），兼容前端旧格式"""
    try:
        rows = chat_service.list_conversations_with_summary(
            DEFAULT_TENANT_ID, channel="xianyu", page_size=200,
        )
    except Exception as e:
        logger.error(f"从数据库读取会话列表失败: {e}")
        return []

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "cid": r["channel_conversation_id"] or "",
            "buyer_name": r["customer_name"] or "",
            "last_msg": (r["last_msg"] or "")[:50],
            "last_time": r["last_time"].isoformat() if r["last_time"] else "",
            "count": r["msg_count"] or 0,
        })
    return result


def get_messages(cid: str) -> list[dict]:
    """返回指定会话的消息列表（从数据库读取），兼容前端旧格式"""
    try:
        msgs = chat_service.get_messages_by_channel_cid(
            DEFAULT_TENANT_ID, cid, page_size=500,
        )
    except Exception as e:
        logger.error(f"从数据库读取消息失败: {e}")
        return []

    return [
        {
            "cid": cid,
            "sender_id": m.sender_name or "",  # 前端用 sender_id 展示
            "sender_name": m.sender_name or "",
            "content": m.content,
            "time": m.created_at.isoformat(),
        }
        for m in msgs
    ]


async def fetch_history(cid: str):
    """拉取指定会话的历史消息并写入数据库"""
    global _live
    if not _live:
        raise RuntimeError("未连接闲鱼")
    history = await _live.list_all_conversations(cid)

    try:
        conv = chat_service.get_or_create_conversation(
            DEFAULT_TENANT_ID, "xianyu", cid,
        )
    except Exception as e:
        logger.error(f"查找会话失败: {e}")
        return 0

    inserted = 0
    for msg in history:
        try:
            chat_service.insert_message_raw(
                DEFAULT_TENANT_ID, conv.id,
                role="customer",
                sender_name=msg.get("sender_name", ""),
                content=msg.get("content", ""),
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"写入历史消息失败: {e}")
    return inserted


async def auto_connect():
    """应用启动时自动连接（如果已保存 Cookie）"""
    cookie = get_saved_cookie()
    if cookie:
        logger.warning("尝试自动连接闲鱼...")
        error = await connect(cookie)
        if error:
            logger.warning(f"自动连接失败: {error}")
        else:
            logger.warning("自动连接闲鱼成功")


async def connect(cookie: str) -> str | None:
    """连接闲鱼。返回 None 表示成功，返回字符串表示错误信息"""
    global _live, _task, _connected_at

    if _live and _task and not _task.done():
        raise RuntimeError("已连接，请先断开")

    cached_token, cached_device_id = get_cached_token()
    if cached_token and _cookie_changed(cookie):
        logger.warning("Cookie 已变更，清除旧 token")
        cached_token = ""
    save_cookie_to_file(cookie, token=cached_token, device_id=cached_device_id)

    async def _on_registered():
        logger.warning("[on_ready] IM 注册完成，开始拉取会话")
        await _fetch_conversations()

    _live = XianyuLive(cookie, on_message=_on_message,
                       cached_token=cached_token, cached_device_id=cached_device_id,
                       on_ready=_on_registered)
    _task = asyncio.create_task(_live.main())
    _connected_at = datetime.now(timezone.utc)

    error = await _live.wait_ready(timeout=300)
    if error:
        await disconnect()
        return error

    token = _live.xianyu._cached_token
    if token:
        save_cookie_to_file(cookie, token=token, device_id=_live.device_id)

    logger.info("闲鱼连接已启动，等待 IM 注册完成后拉取会话")
    return None


async def _fetch_conversations():
    global _live
    logger.warning("[拉会话] _fetch_conversations 已启动")
    try:
        convs = await _live.list_newest_conversations()
        logger.warning(f"[拉会话] list_newest_conversations 返回 {len(convs)} 条")

        for i, c in enumerate(convs):
            cid = c["cid"]
            if not cid:
                continue
            try:
                chat_service.get_or_create_conversation(
                    DEFAULT_TENANT_ID, "xianyu", cid,
                    customer_name=c.get("buyer_name", ""),
                    customer_id=c.get("buyer_id", ""),
                )
            except Exception as e:
                logger.warning(f"[拉会话] 创建会话失败 cid={cid}: {e}")
                continue

            if i > 0:
                await asyncio.sleep(1.5)
            try:
                history = await _live.list_all_conversations(cid)
                if history:
                    conv = chat_service.get_or_create_conversation(
                        DEFAULT_TENANT_ID, "xianyu", cid,
                    )
                    for msg in history:
                        try:
                            chat_service.insert_message_raw(
                                DEFAULT_TENANT_ID, conv.id,
                                role="customer",
                                sender_name=msg.get("sender_name", ""),
                                content=msg.get("content", ""),
                            )
                        except Exception:
                            pass
                    logger.warning(f"[拉历史] cid={cid} 写入 {len(history)} 条消息到数据库")
            except Exception as e:
                logger.warning(f"[拉历史] cid={cid} 失败: {e}")

        logger.info(f"会话列表已同步到数据库，共 {len(convs)} 个会话")
    except Exception as e:
        logger.warning(f"拉取会话列表失败: {e}")


async def disconnect():
    global _live, _task, _connected_at

    if not _live:
        return

    _live.stop()
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass

    _live = None
    _task = None
    _connected_at = None
    logger.info("闲鱼连接已断开")
