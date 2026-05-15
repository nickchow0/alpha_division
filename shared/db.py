import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extensions import connection

_pool: Optional[SimpleConnectionPool] = None
_lock = threading.Lock()


def get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                dsn = os.environ.get("DATABASE_URL")
                if not dsn:
                    raise RuntimeError("DATABASE_URL environment variable is not set")
                _pool = SimpleConnectionPool(minconn=1, maxconn=5, dsn=dsn)
    return _pool


@contextmanager
def get_conn() -> Iterator[connection]:
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
