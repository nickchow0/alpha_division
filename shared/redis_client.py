import os
import threading
import redis
from typing import Optional

_client: Optional[redis.Redis] = None
_lock = threading.Lock()


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = redis.Redis.from_url(
                    os.getenv("REDIS_URL", "redis://localhost:6379"),
                    decode_responses=True,
                )
    return _client
