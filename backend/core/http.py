"""全局共享 aiohttp session — 全应用一个连接池，连接复用"""
import logging

import aiohttp

logger = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None


async def get_http_session() -> aiohttp.ClientSession:
    """懒加载全局 session，进程一生只创建一次；之后所有请求复用其连接池"""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60),  # 兜底总超时，per-request timeout 优先
            connector=aiohttp.TCPConnector(limit=50, ttl_dns_cache=300),  # 并发上限 + DNS 缓存 5 分钟
        )
        logger.info("创建全局 aiohttp session")
    return _session


async def close_http_session():
    """应用关闭时释放连接池（session.close 会同时关闭 connector，勿重复关闭）"""
    global _session
    if _session and not _session.closed:
        await _session.close()
        logger.info("已关闭全局 aiohttp session")
    _session = None
