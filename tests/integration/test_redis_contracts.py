"""
Integration tests: Redis key and stream contracts.

Verifies the exact Redis patterns each service uses so that a change in one
service's writer is caught if it breaks another service's reader.
"""
import json
import pytest


# ---------- heartbeat ----------

def test_heartbeat_write_and_read(raw_redis):
    raw_redis.setex("heartbeat:data", 90, "ok")
    assert raw_redis.get("heartbeat:data") == "ok"


def test_heartbeat_ttl_in_range(raw_redis):
    raw_redis.setex("heartbeat:data", 90, "ok")
    ttl = raw_redis.ttl("heartbeat:data")
    assert 85 <= ttl <= 90


def test_missing_heartbeat_returns_negative_ttl(raw_redis):
    # Key doesn't exist -> ttl returns -2
    assert raw_redis.ttl("heartbeat:data") == -2


# ---------- market snapshot stream ----------

_SAMPLE_SNAPSHOT = {
    "symbol": "AAPL",
    "timestamp": "2026-05-17T10:00:00+00:00",
    "price": 150.0,
    "rsi": 45.0,
    "sma20": 148.0,
    "sma50": 145.0,
    "sma20_prev": 147.5,
    "sma20_prev2": 147.0,
    "news": [],
    "macro": {"fed_funds_rate": 4.5, "cpi": 310.0},
}


def test_snapshot_stream_publish_and_read(raw_redis):
    raw_redis.xadd("stream:market_snapshot", {"data": json.dumps(_SAMPLE_SNAPSHOT)})
    messages = raw_redis.xread({"stream:market_snapshot": "0"}, count=1)
    assert messages is not None
    _, entries = messages[0]
    _, fields = entries[0]
    data = json.loads(fields["data"])
    assert data["symbol"] == "AAPL"
    assert data["price"] == 150.0
    assert "rsi" in data
    assert "macro" in data


def test_snapshot_stream_consumer_group(raw_redis):
    raw_redis.xadd("stream:market_snapshot", {"data": json.dumps(_SAMPLE_SNAPSHOT)})
    raw_redis.xgroup_create("stream:market_snapshot", "analysis-group", id="0", mkstream=True)

    results = raw_redis.xreadgroup(
        "analysis-group", "analysis-1", {"stream:market_snapshot": ">"}, count=1
    )
    assert results is not None
    _, messages = results[0]
    msg_id, fields = messages[0]
    data = json.loads(fields["data"])
    assert data["symbol"] == "AAPL"

    # Ack clears the pending entry
    raw_redis.xack("stream:market_snapshot", "analysis-group", msg_id)
    pending = raw_redis.xpending("stream:market_snapshot", "analysis-group")
    assert pending["pending"] == 0


def test_snapshot_stream_busygroup_ignored(raw_redis):
    raw_redis.xadd("stream:market_snapshot", {"data": json.dumps(_SAMPLE_SNAPSHOT)})
    raw_redis.xgroup_create("stream:market_snapshot", "analysis-group", id="0", mkstream=True)
    # Second create should not raise — matches _ensure_group() behaviour
    try:
        raw_redis.xgroup_create("stream:market_snapshot", "analysis-group", id="0", mkstream=True)
    except Exception as exc:
        assert "BUSYGROUP" in str(exc)


# ---------- signals stream ----------

_SAMPLE_SIGNAL = {
    "symbol": "AAPL",
    "decision": "buy",
    "confidence": 0.85,
    "decision_id": 1,
    "published_at": "2026-05-17T10:00:00+00:00",
}


def test_signal_stream_publish_and_read(raw_redis):
    raw_redis.xadd("stream:signals", {"data": json.dumps(_SAMPLE_SIGNAL)})
    messages = raw_redis.xread({"stream:signals": "0"}, count=1)
    assert messages is not None
    _, entries = messages[0]
    _, fields = entries[0]
    data = json.loads(fields["data"])
    assert data["symbol"] == "AAPL"
    assert data["decision"] in ("buy", "sell")
    assert data["confidence"] >= 0.65
    assert "decision_id" in data
    assert "published_at" in data


def test_signal_stream_consumer_group(raw_redis):
    raw_redis.xadd("stream:signals", {"data": json.dumps(_SAMPLE_SIGNAL)})
    raw_redis.xgroup_create("stream:signals", "execution-group", id="0", mkstream=True)
    results = raw_redis.xreadgroup(
        "execution-group", "execution-1", {"stream:signals": ">"}, count=1
    )
    assert results is not None


# ---------- alert state keys ----------

def test_crash_suspect_key(raw_redis):
    raw_redis.setex("alert:crash_suspect:data", 90, "1")
    assert raw_redis.exists("alert:crash_suspect:data") == 1
    ttl = raw_redis.ttl("alert:crash_suspect:data")
    assert ttl > 0


def test_crash_key(raw_redis):
    raw_redis.setex("alert:crash:data", 3600, "1")
    assert raw_redis.exists("alert:crash:data") == 1
    ttl = raw_redis.ttl("alert:crash:data")
    assert ttl > 3500
