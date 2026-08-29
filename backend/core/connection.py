import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://saas:saas@localhost:5432/saas")

_pool: ThreadedConnectionPool | None = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(2, 10, dsn=DATABASE_URL)
    return _pool


@contextmanager
def get_conn():
    pool = _get_pool()
    conn = pool.getconn()
    conn.cursor_factory = RealDictCursor
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
