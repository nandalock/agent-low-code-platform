"""闲鱼连接管理器：单例管理 XianyuLive 的启停 + 消息存储"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.xianyu.live import XianyuLive

logger = logging.getLogger(__name__)

# Cookie 持久化文件（在项目根目录）
if os.path.exists("/app"):
    _COOKIE_FILE = Path("/app/xianyu_cookie.json")
else:
    _COOKIE_FILE = Path(__file__).parent.parent.parent / "xianyu_cookie.json"

# 连接状态
_live: XianyuLive | None = None
_task: asyncio.Task | None = None
_connected_at: datetime | None = None

# 消息存储：cid → [消息, ...]
_conversations: dict[str, list[dict]] = {}

# 前端 WebSocket 订阅者列表
_subscribers: list = []


def subscribe(callback):
    """注册前端 WebSocket 消息回调"""
    _subscribers.append(callback)


def unsubscribe(callback):
    """取消注册"""
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
    """读取保存的 Cookie"""
    try:
        if _COOKIE_FILE.exists():
            data = json.loads(_COOKIE_FILE.read_text("utf-8"))
            return data.get("cookie", "")
    except Exception:
        pass
    return ""


def get_cached_token() -> tuple[str, str]:
    """读取缓存的 token 和 device_id"""
    try:
        if _COOKIE_FILE.exists():
            data = json.loads(_COOKIE_FILE.read_text("utf-8"))
            return data.get("token", ""), data.get("device_id", "")
    except Exception:
        pass
    return "", ""


def save_cookie_to_file(cookie: str, token: str = "", device_id: str = ""):
    """保存 Cookie 到文件（含可选的 token 缓存）"""
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
    """收到新消息，存入内存，并通知前端 WebSocket 订阅者"""
    cid = msg["cid"]
    if not cid:
        return
    msg["time"] = datetime.now(timezone.utc).isoformat()
    if cid not in _conversations:
        _conversations[cid] = []
    _conversations[cid].append(msg)
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
    """返回会话列表，每个会话含最新消息摘要"""
    result = []
    for cid, msgs in _conversations.items():
        last = msgs[-1] if msgs else {}
        result.append({
            "cid": cid,
            "buyer_name": last.get("sender_name", ""),
            "last_msg": last.get("content", "")[:50],
            "last_time": last.get("time", ""),
            "count": len(msgs),
        })
    def _ts(x):
        t = x.get("last_time", 0)
        return t if isinstance(t, (int, float)) else 0
    result.sort(key=_ts, reverse=True)
    return result


def get_messages(cid: str) -> list[dict]:
    return _conversations.get(cid, [])


async def fetch_history(cid: str):
    """拉取指定会话的历史消息并存入内存"""
    global _live
    if not _live:
        raise RuntimeError("未连接闲鱼")
    history = await _live.list_all_conversations(cid)
    existing = _conversations.get(cid, [])
    existing_ids = {(m.get("content"), m.get("time")) for m in existing}
    for msg in history:
        key = (msg.get("content"), msg.get("time"))
        if key not in existing_ids:
            msg["time"] = datetime.now(timezone.utc).isoformat()
            existing.insert(0, msg)
    _conversations[cid] = existing
    return len(history)


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
    global _live, _task, _connected_at, _conversations

    if _live and _task and not _task.done():
        raise RuntimeError("已连接，请先断开")

    cached_token, cached_device_id = get_cached_token()
    # 如果 cookie 变了（_m_h5_tk 不同），旧 token 自动失效
    if cached_token and _cookie_changed(cookie):
        logger.warning("Cookie 已变更，清除旧 token")
        cached_token = ""
    save_cookie_to_file(cookie, token=cached_token, device_id=cached_device_id)

    async def _on_registered():
        """IM 注册完成后拉取会话"""
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

    # 持久化 token
    token = _live.xianyu._cached_token
    if token:
        save_cookie_to_file(cookie, token=token, device_id=_live.device_id)

    logger.info("闲鱼连接已启动，等待 IM 注册完成后拉取会话")
    return None


async def _fetch_conversations():
    global _live, _conversations
    logger.warning("[拉会话] _fetch_conversations 已启动")
    try:
        convs = await _live.list_newest_conversations()
        logger.warning(f"[拉会话] list_newest_conversations 返回 {len(convs)} 条")
        for c in convs:
            cid = c["cid"]
            if cid and cid not in _conversations:
                _conversations[cid] = []
        logger.info(f"加载 {len(convs)} 个会话")
        # 拉取每个会话的完整聊天记录
        for i, c in enumerate(convs):
            cid = c["cid"]
            if not cid or _conversations.get(cid):
                continue
            try:
                if i > 0:
                    await asyncio.sleep(1.5)  # 避免请求太密集
                history = await _live.list_all_conversations(cid)
                if history:
                    _conversations[cid] = history
                    logger.warning(f"[拉历史] cid={cid} 拉取 {len(history)} 条消息")
            except Exception as e:
                logger.warning(f"[拉历史] cid={cid} 失败: {e}")
                _conversations[cid] = []
    except Exception as e:
        logger.warning(f"拉取会话列表失败: {e}")


async def disconnect():
    global _live, _task, _connected_at, _conversations

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
    _conversations = {}
    logger.info("闲鱼连接已断开")
