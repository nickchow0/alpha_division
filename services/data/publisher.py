"""Publisher module for pushing market snapshots to Redis streams."""

import json
from shared.redis_client import get_redis

# Named constants
_STREAM_KEY = "stream:market_snapshot"
_STREAM_MAXLEN = 1000
_HEARTBEAT_KEY = "heartbeat:data"
_HEARTBEAT_TTL = 90
_ACCOUNT_EQUITY_KEY = "account:equity"


def publish_snapshot(snapshot: dict) -> None:
    """
    Publish a market snapshot to the Redis stream and cache the latest
    snapshot per-symbol so consumers (e.g. dashboard) can read it directly.

    Args:
        snapshot: Dictionary containing market snapshot data with fields like
                 symbol, timestamp, price, rsi, sma20, sma50, sma20_prev,
                 sma20_prev2, news, macro.
    """
    r = get_redis()
    payload = json.dumps(snapshot)
    r.xadd(
        _STREAM_KEY,
        {"data": payload},
        maxlen=_STREAM_MAXLEN,
    )
    # Cache latest snapshot per symbol with no expiry — overwritten each cycle
    symbol = snapshot.get("symbol", "UNKNOWN")
    r.set(f"snapshot:{symbol}", payload)


def publish_account_equity(equity: float) -> None:
    """
    Cache the current Alpaca account equity in Redis.

    Written on every price cycle so the dashboard always has a fresh,
    authoritative portfolio value without reconstructing it from snapshots.
    """
    r = get_redis()
    r.set(_ACCOUNT_EQUITY_KEY, str(equity))


def publish_heartbeat() -> None:
    """
    Publish a heartbeat signal to Redis.

    Sets a key with a TTL to indicate the data service is alive.
    """
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")
