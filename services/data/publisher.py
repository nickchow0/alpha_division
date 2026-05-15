"""Publisher module for pushing market snapshots to Redis streams."""

import json
from shared.redis_client import get_redis

# Named constants
_STREAM_KEY = "stream:market_snapshot"
_STREAM_MAXLEN = 1000
_HEARTBEAT_KEY = "heartbeat:data"
_HEARTBEAT_TTL = 90


def publish_snapshot(snapshot: dict) -> None:
    """
    Publish a market snapshot to Redis stream.

    Args:
        snapshot: Dictionary containing market snapshot data with fields like
                 symbol, timestamp, price, rsi, sma20, sma50, sma20_prev,
                 sma20_prev2, news, macro.
    """
    r = get_redis()
    r.xadd(
        _STREAM_KEY,
        {"data": json.dumps(snapshot)},
        maxlen=_STREAM_MAXLEN,
    )


def publish_heartbeat() -> None:
    """
    Publish a heartbeat signal to Redis.

    Sets a key with a TTL to indicate the data service is alive.
    """
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")
