import json
from typing import List

from shared.redis_client import get_redis
from shared.logger import get_logger

log = get_logger("execution")

_STREAM_KEY = "stream:signals"
_GROUP_NAME = "execution-group"
_CONSUMER_NAME = "execution-1"


def _ensure_group() -> None:
    """
    Create the consumer group if it doesn't exist.

    Uses id="$" so on first creation only new signals are consumed —
    the Execution Service won't re-process old signals from before it started.
    Ignores BUSYGROUP error (group already exists from a previous run).
    """
    r = get_redis()
    try:
        r.xgroup_create(_STREAM_KEY, _GROUP_NAME, id="$", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def read_next_signals(count: int = 10, block_ms: int = 5000) -> List[dict]:
    """
    Read up to `count` unprocessed signals from stream:signals.

    Blocks for up to `block_ms` milliseconds if no new signals are available.
    Malformed messages (missing 'data' field, invalid JSON) are logged,
    acknowledged, and skipped so they don't block the consumer group.

    Each returned signal dict has an extra "_msg_id" key. Pass it to
    ack_signal() after the signal has been processed (success or failure).

    Returns an empty list if no signals arrived within block_ms.
    """
    _ensure_group()
    r = get_redis()

    results = r.xreadgroup(
        _GROUP_NAME,
        _CONSUMER_NAME,
        {_STREAM_KEY: ">"},
        count=count,
        block=block_ms,
    )

    signals = []
    if not results:
        return signals

    for _stream_name, messages in results:
        for msg_id, fields in messages:
            try:
                data = fields.get("data")
                if data is None:
                    raise ValueError("Missing 'data' field in stream message")
                signal = json.loads(data)
                signal["_msg_id"] = msg_id
                signals.append(signal)
            except Exception as exc:
                log.error(f"Malformed signal {msg_id}, skipping: {exc}")
                r.xack(_STREAM_KEY, _GROUP_NAME, msg_id)

    return signals


def ack_signal(msg_id: str) -> None:
    """
    Acknowledge that a signal has been fully processed.

    Must be called after every successfully read signal (success or error in
    processing) so the consumer group doesn't re-deliver it after a restart.
    """
    r = get_redis()
    r.xack(_STREAM_KEY, _GROUP_NAME, msg_id)
