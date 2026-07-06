import json
from typing import List

from shared.redis_client import get_redis
from shared.logger import get_logger

log = get_logger("analysis")

_STREAM_KEY = "stream:market_snapshot"
_GROUP_NAME = "analysis-group"
_CONSUMER_NAME = "analysis-1"


def _ensure_group() -> None:
    """
    Create the consumer group if it doesn't exist.

    Uses id="$" so on first creation only NEW messages are consumed —
    the Analysis Service won't re-process historical snapshots on first start.

    Ignores BUSYGROUP error (group already exists from a previous run).
    Raises any other exception so startup fails fast on genuine Redis errors.
    """
    r = get_redis()
    try:
        r.xgroup_create(_STREAM_KEY, _GROUP_NAME, id="$", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def read_next_snapshots(count: int = 10, block_ms: int = 5000) -> List[dict]:
    """
    Read up to `count` unprocessed snapshots from the market stream.

    Blocks for up to `block_ms` milliseconds if no new messages are available.
    Malformed messages (missing 'data' field, invalid JSON) are logged,
    acknowledged, and skipped — they never block the consumer group.

    Each returned snapshot dict has an extra "_msg_id" key with the Redis
    stream message ID. Pass it to ack_snapshot() after processing.

    Returns an empty list if no messages arrived within block_ms.
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

    snapshots = []
    if not results:
        return snapshots

    for _stream_name, messages in results:
        for msg_id, fields in messages:
            try:
                data = fields.get("data")
                if data is None:
                    raise ValueError("Missing 'data' field in stream message")
                snapshot = json.loads(data)
                snapshot["_msg_id"] = msg_id
                snapshots.append(snapshot)
            except Exception as exc:
                log.error(f"Malformed snapshot {msg_id}, skipping: {exc}")
                r.xack(_STREAM_KEY, _GROUP_NAME, msg_id)

    return snapshots


def ack_snapshot(msg_id: str) -> None:
    """
    Acknowledge that a snapshot has been fully processed.

    Must be called after every read message (whether processing succeeded
    or failed) so the consumer group doesn't re-deliver it after a restart.
    """
    r = get_redis()
    r.xack(_STREAM_KEY, _GROUP_NAME, msg_id)
