import os
import logging
from typing import Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

_pool: Optional[redis.ConnectionPool] = None
_client: Optional[redis.Redis] = None

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


async def get_redis() -> redis.Redis:
    global _pool, _client

    if _client is None:
        _pool = redis.ConnectionPool.from_url(
            REDIS_URL,
            max_connections=50,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=3,
            retry_on_timeout=True,
            protocol=2,
        )
        _client = redis.Redis(connection_pool=_pool)

        try:
            await _client.ping()
            logger.info(f"Redis 连接成功: {REDIS_URL}")
        except Exception as e:
            logger.error(f"Redis 连接失败: {e}")
            raise

    return _client


async def close_redis():
    global _pool, _client

    if _client:
        await _client.close()
        _client = None

    if _pool:
        await _pool.disconnect()
        _pool = None

    logger.info("Redis 连接已关闭")
