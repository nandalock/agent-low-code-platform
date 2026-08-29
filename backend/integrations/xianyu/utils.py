"""闲鱼工具函数 — 纯 Python 实现，无 JS 依赖"""
import base64
import hashlib
import json
import os
import random
import time
from typing import Any


def trans_cookies(cookies_str: str) -> dict:
    cookies = {}
    for item in cookies_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            if k.strip():
                cookies[k.strip()] = v.strip()
    return cookies


def get_session_cookies_str(session) -> str:
    cookies = session.cookies.get_dict()
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def generate_mid() -> str:
    """生成消息ID（与参考项目格式一致：random+timestamp空格0）"""
    random_part = int(1000 * random.random())
    timestamp = int(time.time() * 1000)
    return f"{random_part}{timestamp} 0"


def generate_uuid() -> str:
    return f"-{int(time.time() * 1000)}1"


def generate_device_id(user_id: str) -> str:
    import string as _string
    return f"{user_id}_{''.join(random.choices(_string.hexdigits.lower(), k=32))}"


def generate_sign(t: str, token: str, data: str) -> str:
    app_key = "34839810"
    msg = f"{token}&{t}&{app_key}&{data}"
    return hashlib.md5(msg.encode("utf-8")).hexdigest()


def decrypt(data: str) -> str:
    """解密 MessagePack + base64 消息"""
    try:
        decoded = base64.b64decode(data)
    except Exception:
        padding = len(data) % 4
        if padding:
            data += "=" * (4 - padding)
        decoded = base64.b64decode(data)

    # 简单的 MessagePack 解码转 JSON
    decoder = _MsgPackDecoder(decoded)
    value = decoder.decode()
    return json.dumps(_convert(value), ensure_ascii=False)


class _MsgPackDecoder:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read_byte(self) -> int:
        b = self.data[self.pos]
        self.pos += 1
        return b

    def read_bytes(self, n: int) -> bytes:
        r = self.data[self.pos : self.pos + n]
        self.pos += n
        return r

    def decode(self) -> Any:
        b = self.read_byte()
        if b <= 0x7F:
            return b
        if 0x80 <= b <= 0x8F:
            return {self.decode(): self.decode() for _ in range(b & 0x0F)}
        if 0x90 <= b <= 0x9F:
            return [self.decode() for _ in range(b & 0x0F)]
        if 0xA0 <= b <= 0xBF:
            return self.read_bytes(b & 0x1F).decode("utf-8")
        if b == 0xC0:
            return None
        if b == 0xC2:
            return False
        if b == 0xC3:
            return True
        if b == 0xCC:
            return self.read_byte()
        if b == 0xCD:
            return int.from_bytes(self.read_bytes(2), "big")
        if b == 0xCE:
            return int.from_bytes(self.read_bytes(4), "big")
        if b == 0xD9:
            return self.read_bytes(self.read_byte()).decode("utf-8")
        if b == 0xDC:
            return [self.decode() for _ in range(int.from_bytes(self.read_bytes(2), "big"))]
        if b == 0xDE:
            return {self.decode(): self.decode() for _ in range(int.from_bytes(self.read_bytes(2), "big"))}
        if b >= 0xE0:
            return b - 0x100
        raise ValueError(f"Unknown msgpack byte: {b:02x}")


def _convert(obj: Any) -> Any:
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.hex()
    if isinstance(obj, dict):
        return {_convert(k): _convert(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert(v) for v in obj]
    return obj
