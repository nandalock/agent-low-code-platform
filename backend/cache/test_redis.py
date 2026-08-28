"""Quick smoke test for Redis connection and basic ops.

Usage:  python backend/cache/test_redis.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend.cache.redis_client import get_redis, close_redis


async def main():
    print("1. 连接测试...")
    r = await get_redis()
    pong = await r.ping()
    assert pong, "PING failed"
    print("   OK — PING 成功")

    print("2. 写入测试...")
    await r.set("test:hello", "world", ex=60)
    val = await r.get("test:hello")
    assert val == "world", f"预期 world，实际 {val}"
    print(f"   OK — SET/GET 正常，值={val}")

    print("3. 过期测试...")
    ttl = await r.ttl("test:hello")
    assert 0 < ttl <= 60, f"TTL 异常: {ttl}"
    print(f"   OK — TTL={ttl}s")

    print("4. 清理...")
    await r.delete("test:hello")
    gone = await r.get("test:hello")
    assert gone is None
    print("   OK — DELETE 正常")

    await close_redis()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
