import os
import time
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://saas:saas@localhost:5432/saas")

_pool: ThreadedConnectionPool | None = None
# 前端页面加载会并行打十几个请求（agents/config/mcp 列表/tools），
# 池满时 psycopg2 直接抛 PoolError 不排队，容量开大 + 短重试兜底
_MAX_CONNECTIONS = 50
_GETCONN_RETRIES = 5


def _get_pool():
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(2, _MAX_CONNECTIONS, dsn=DATABASE_URL)
    return _pool


@contextmanager
def get_conn():
    pool = _get_pool()
    conn = None
    last_err = None
    for attempt in range(_GETCONN_RETRIES):
        try:
            conn = pool.getconn()
            break
        except psycopg2.pool.PoolError as e:
            last_err = e
            time.sleep(0.1 * (attempt + 1))  # 突发并发时等一个连接释放
    if conn is None:
        raise last_err
    conn.cursor_factory = RealDictCursor
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
