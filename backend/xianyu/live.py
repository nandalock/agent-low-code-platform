import asyncio
import base64
import json
import logging
import threading
import time

import websockets

from backend.xianyu.apis import XianyuApis
from backend.xianyu.utils import (
    decrypt,
    generate_device_id,
    generate_mid,
    generate_uuid,
    trans_cookies,
)

logger = logging.getLogger(__name__)

WS_URL = "wss://wss-goofish.dingtalk.com/"


_INVALID_NICK_SET = {
    "交易消息", "系统消息", "卡片消息",
    "我完成了评价", "对方完成了评价", "快给ta一个评价吧～",
    "卖家已发货", "买家已付款", "买家已确认收货", "等待您发货",
    "超时未付款，系统关闭了订单",
}


def _is_valid_nick(name: str) -> bool:
    if not name or not name.strip():
        return False
    stripped = name.strip()
    if stripped.isdigit():
        return False
    if stripped in _INVALID_NICK_SET:
        return False
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1]
        if inner in _INVALID_NICK_SET:
            return False
    return True


def _extract_message_summary(message: dict) -> str:
    """从 lastMessage.message 提取摘要文本（参考项目逻辑）"""
    try:
        content = message.get("content", {}) or {}
        custom = content.get("custom", {}) or {}
        summary = custom.get("summary", "")
        if summary:
            return summary[:50]
        custom_data = custom.get("data", "")
        if custom_data:
            try:
                decoded = json.loads(base64.b64decode(custom_data).decode("utf-8"))
                if "text" in decoded:
                    text_obj = decoded["text"]
                    if isinstance(text_obj, dict):
                        return text_obj.get("text", "")[:50]
                    else:
                        return str(text_obj)[:50]
            except Exception:
                pass
    except Exception:
        pass
    return ""


class XianyuLive:
    def __init__(self, cookies_str: str, on_message=None,
                 cached_token: str = "", cached_device_id: str = "",
                 on_ready=None):
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        self.myid = self.cookies["unb"]
        self.device_id = cached_device_id or generate_device_id(self.myid)
        self.xianyu = XianyuApis(cookies_str, self.device_id)
        self._cached_token = cached_token
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._init_error: str | None = None
        self._ws = None
        self._on_message = on_message
        self._on_ready = on_ready
        self._pending_mid_futures: dict[str, asyncio.Future] = {}
        self._recv_task = None
        self._heartbeat_task = None

    def stop(self):
        self._stop_event.set()
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
        if self._ws:
            try:
                asyncio.ensure_future(self._ws.close())
            except Exception:
                pass

    async def wait_ready(self, timeout: float = 20) -> str | None:
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return "连接超时"
        return self._init_error

    async def send_and_wait(self, msg: dict, timeout: float = 15) -> dict | None:
        if not self._ws:
            return None
        mid = msg["headers"]["mid"]
        lwp = msg.get("lwp", "?")
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_mid_futures[mid] = future
        try:
            await self._ws.send(json.dumps(msg))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"LWP 请求超时: {lwp}")
            return None
        except Exception as e:
            logger.warning(f"send_and_wait 异常: {e}")
            return None
        finally:
            self._pending_mid_futures.pop(mid, None)

    async def _init_register(self, ws):
        """后台任务：获取 token 并注册 IM（不阻塞连接就绪，失败后 10 分钟自动重试一次）"""
        try:
            # 1. 获取 token（优先用缓存）
            if self._cached_token:
                logger.warning(f"使用缓存 token: {self._cached_token[:30]}...")
                token = self._cached_token
            else:
                token = await self.xianyu.get_token_async()
            if not token:
                logger.warning("获取 accessToken 失败，10分钟后自动重试")
                await asyncio.sleep(600)  # 等10分钟，让反爬冷却
                if not self._stop_event.is_set():
                    asyncio.create_task(self._init_register(ws))
                return

            # 2. 发送 /reg
            reg_mid = generate_mid()
            reg_msg = {
                "lwp": "/reg",
                "headers": {
                    "cache-header": "app-key token ua wv",
                    "app-key": "444e9908a51d1cb236a27862abc769c9",
                    "token": token,
                    "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "dt": "j",
                    "wv": "im:3,au:3,sy:6",
                    "sync": "0,0;0;0;",
                    "did": self.device_id,
                    "mid": reg_mid,
                },
            }
            reg_resp = await self.send_and_wait(reg_msg, timeout=8)
            if reg_resp:
                reg_code = reg_resp.get("code", 0)
                logger.warning(f"注册响应: code={reg_code}, body={json.dumps(reg_resp.get('body', {}), ensure_ascii=False)[:200]}")
            else:
                logger.warning("注册响应超时，继续尝试")

            # 3. 发送 ackDiff
            current_time = int(time.time() * 1000)
            await ws.send(json.dumps({
                "lwp": "/r/SyncStatus/ackDiff",
                "headers": {"mid": generate_mid()},
                "body": [
                    {
                        "pipeline": "sync",
                        "tooLong2Tag": "PNM,1",
                        "channel": "sync",
                        "topic": "sync",
                        "highPts": 0,
                        "pts": current_time * 1000,
                        "seq": 0,
                        "timestamp": current_time,
                    }
                ],
            }))
            await asyncio.sleep(2)
            logger.warning(f"闲鱼 IM 注册完成 (token={token[:30]}...)")
            if self._on_ready:
                asyncio.create_task(self._on_ready())
        except Exception as e:
            logger.warning(f"IM 注册异常: {e}")

    async def heart_beat(self, ws):
        while not self._stop_event.is_set():
            try:
                msg = {"lwp": "/!", "headers": {"mid": generate_mid()}}
                await ws.send(json.dumps(msg))
            except Exception:
                break
            await asyncio.sleep(15)

    def user_alive(self):
        while not self._stop_event.is_set():
            time.sleep(600)
            if self._stop_event.is_set():
                break
            try:
                self.xianyu.refresh_token()
            except Exception as e:
                logger.warning(f"refresh_token 失败: {e}")

    async def _message_loop(self, ws):
        """消息接收循环（与参考项目一致）"""
        try:
            async for raw_message in ws:
                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError:
                    continue

                headers = message.get("headers", {})
                mid = headers.get("mid", "") if isinstance(headers, dict) else ""
                lwp = message.get("lwp", "")
                b = message.get("body", {})
                btype = "list" if isinstance(b, list) else "dict"
                logger.warning(f"[<-] lwp={lwp} mid={mid[:30]} body={btype} {str(b)[:120]}")

                # 发送 ACK
                ack = {
                    "code": 200,
                    "headers": {
                        "mid": mid or generate_mid(),
                        "sid": headers.get("sid", ""),
                    },
                }
                for k in ("app-key", "ua", "dt"):
                    if k in headers:
                        ack["headers"][k] = headers[k]
                try:
                    await ws.send(json.dumps(ack))
                except Exception:
                    pass

                # 心跳响应
                if "body" not in message and message.get("code") == 200:
                    continue

                # LWP 请求-响应匹配（服务端会在 headers.mid 带回相同 mid）
                if mid and mid in self._pending_mid_futures:
                    future = self._pending_mid_futures.pop(mid, None)
                    if future and not future.done():
                        logger.warning(f"[LWP匹配] 命中! mid={mid[:30]}")
                        future.set_result(message)
                    continue

                # 推送消息
                await self._handle_push_message(message)

        except asyncio.CancelledError:
            pass
        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            logger.warning(f"消息循环异常: {e}")

    async def _handle_push_message(self, message: dict):
        try:
            raw = message["body"]["syncPushPackage"]["data"][0]["data"]
        except Exception:
            return

        # base64 解码
        try:
            decoded = base64.b64decode(raw).decode("utf-8")
            parsed = json.loads(decoded)
            msg_data = self._extract_message(parsed)
            if msg_data and self._on_message:
                await self._on_message(msg_data)
            return
        except Exception:
            pass

        # protobuf 旧格式
        try:
            decrypted = decrypt(raw)
            msg = json.loads(decrypted)
            cid = msg.get("1", {}).get("2", "").split("@")[0]
            inner = msg.get("1", {}).get("10", {})
            msg_data = {
                "cid": cid,
                "sender_id": str(inner.get("senderUserId", "")),
                "sender_name": inner.get("reminderTitle", ""),
                "content": inner.get("reminderContent", ""),
                "time": "",
            }
            if msg_data["cid"] and self._on_message:
                await self._on_message(msg_data)
        except Exception:
            pass

        # 直接 JSON
        try:
            parsed = json.loads(raw)
            msg_data = self._extract_message(parsed)
            if msg_data and self._on_message:
                await self._on_message(msg_data)
        except Exception:
            pass

    def _extract_message(self, obj, cid=""):
        if not isinstance(obj, dict):
            return None
        if not cid:
            for key in ("cid", "conversationId", "conversationID", "chatId"):
                if key in obj:
                    cid = str(obj[key]).split("@")[0]
                    break
        if not cid:
            return None
        sender_id = ""
        sender_name = ""
        content = ""
        for key in ("senderUserId", "senderId", "fromUserId"):
            if key in obj:
                sender_id = str(obj[key])
                break
        for key in ("senderUserName", "senderName", "reminderTitle"):
            if key in obj:
                sender_name = str(obj[key])
                break
        for key in ("content", "text", "reminderContent", "messageContent"):
            if key in obj:
                content = str(obj[key])
                break
        if not content:
            return None
        logger.info(f"[闲鱼消息] {sender_name}({sender_id}) cid={cid}: {content[:100]}")
        return {"cid": cid, "sender_id": sender_id, "sender_name": sender_name, "content": content, "time": ""}

    async def list_newest_conversations(self) -> list[dict]:
        logger.warning("[拉会话] 开始拉取...")
        if not self._ws:
            logger.warning("[拉会话] 主 WS 未连接")
            return []
        resp = await self.send_and_wait({
            "lwp": "/r/Conversation/listNewestPagination",
            "headers": {"mid": generate_mid()},
            "body": [9007199254740991, 20],
        }, timeout=15)
        if not resp:
            logger.warning("[拉会话] send_and_wait 返回 None（超时或无响应）")
            return []
        body = resp.get("body", {})
        user_convs = body.get("userConvs", [])
        logger.warning(f"[拉会话] 拿到 {len(user_convs)} 个会话")
        convs = []
        for uc in user_convs:
            inner = uc.get("singleChatUserConversation", uc) if isinstance(uc, dict) else {}
            single_conv = inner.get("singleChatConversation", {})
            cid_raw = single_conv.get("cid", "")
            cid = cid_raw.split("@")[0] if "@" in cid_raw else cid_raw
            if not cid:
                continue

            # 通过 pairFirst/pairSecond 确定对方用户ID（参考项目逻辑）
            pair_first = (single_conv.get("pairFirst", "") or "").split("@")[0]
            pair_second = (single_conv.get("pairSecond", "") or "").split("@")[0]
            other_user_id = pair_second if pair_first == self.myid else pair_first

            # 最后一条消息（参考项目: lastMessage.message）
            last_msg_obj = inner.get("lastMessage", {}) or {}
            last_message = last_msg_obj.get("message", {}) or {}
            last_text = _extract_message_summary(last_message)
            last_time = inner.get("modifyTime", 0)

            # 从最后一条消息的 extension 中提取对方名称（参考项目逻辑）
            last_ext = last_message.get("extension", {}) or {}
            if isinstance(last_ext, str):
                try:
                    last_ext = json.loads(last_ext)
                except (json.JSONDecodeError, TypeError):
                    last_ext = {}
            sender_user_id = str(last_ext.get("senderUserId", "") or "").split("@")[0]
            reminder_title = last_ext.get("reminderTitle", "") or ""
            if sender_user_id and sender_user_id == other_user_id and _is_valid_nick(reminder_title):
                other_user_name = reminder_title
            else:
                other_user_name = ""

            convs.append({
                "cid": cid,
                "buyer_name": other_user_name,
                "buyer_id": other_user_id,
                "last_msg": last_text[:80],
                "last_time": last_time,
            })
        return convs

    async def list_all_conversations(self, cid):
        if not self._ws:
            return []
        mid = generate_mid()
        resp = await self.send_and_wait({
            "lwp": "/r/MessageManager/listUserMessages",
            "headers": {"mid": mid},
            "body": [f"{cid}@goofish", False, 9007199254740991, 20, False],
        }, timeout=10)
        if not resp:
            return []
        messages = []
        body = resp.get("body", {})
        for um in body.get("userMessageModels", []):
            try:
                ext = um["message"]["extension"]
                content_b64 = um["message"]["content"]["custom"]["data"]
                content_json = json.loads(base64.b64decode(content_b64).decode("utf-8"))
                inner_text = ""
                if content_json.get("contentType") == 1:
                    inner_text = content_json.get("text", {}).get("text", "")
                messages.insert(0, {
                    "cid": cid,
                    "sender_id": str(ext.get("senderUserId", "")),
                    "sender_name": ext.get("reminderTitle", ""),
                    "content": inner_text,
                    "time": "",
                })
            except Exception:
                continue
        return messages

    async def main(self):
        self._stop_event.clear()

        headers = {
            "Cookie": self.cookies_str,
            "Host": "wss-goofish.dingtalk.com",
            "Connection": "Upgrade",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://www.goofish.com",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        user_alive_thread = threading.Thread(target=self.user_alive, daemon=True)
        user_alive_thread.start()

        try:
            async with websockets.connect(
                WS_URL,
                additional_headers=headers,
                open_timeout=30,
                ping_interval=20,
                ping_timeout=15,
            ) as ws:
                self._ws = ws
                logger.warning("闲鱼 WebSocket 已连接")

                # 1. 立即启动消息循环和心跳
                self._recv_task = asyncio.create_task(self._message_loop(ws))
                self._heartbeat_task = asyncio.create_task(self.heart_beat(ws))

                # 2. 标记就绪（不等待 token/reg）
                self._ready_event.set()
                logger.warning("闲鱼 WebSocket 已就绪")

                # 3. 后台获取 token 并注册 IM
                asyncio.create_task(self._init_register(ws))

                # 4. 等待停止信号
                await self._stop_event.wait()

        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            logger.warning(f"WebSocket 连接异常: {e}")
        finally:
            self._ws = None
            if self._recv_task and not self._recv_task.done():
                self._recv_task.cancel()
                try:
                    await self._recv_task
                except asyncio.CancelledError:
                    pass
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
            await self.xianyu.close()
            logger.info("闲鱼 WebSocket 已断开")
