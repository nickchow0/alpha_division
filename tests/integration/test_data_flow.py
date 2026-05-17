"""
Integration tests: end-to-end data flow contracts.

Uses the actual publisher and signal_writer modules (with test Redis/DB)
to verify the full data pipeline write->read contract.
"""
import json
import pytest

from publisher import publish_snapshot, publish_heartbeat
from signal_writer import write_decision, write_signal, CONFIDENCE_THRESHOLD


_SNAPSHOT = {
    "symbol": "MSFT",
    "timestamp": "2026-05-17T10:00:00+00:00",
    "price": 420.0,
    "rsi": 55.0,
    "sma20": 418.0,
    "sma50": 410.0,
    "sma20_prev": 417.0,
    "sma20_prev2": 416.0,
    "news": [{"headline": "MSFT beats earnings", "datetime": 1715000000}],
    "macro": {"fed_funds_rate": 4.5, "cpi": 310.0},
}


def test_publish_snapshot_writes_to_stream(raw_redis):
    publish_snapshot(_SNAPSHOT)
    messages = raw_redis.xread({"stream:market_snapshot": "0"}, count=1)
    assert messages is not None
    _, entries = messages[0]
    _, fields = entries[0]
    data = json.loads(fields["data"])
    assert data["symbol"] == "MSFT"
    assert data["price"] == 420.0
    assert "news" in data
    assert "macro" in data


def test_publish_heartbeat_sets_key_with_ttl(raw_redis):
    publish_heartbeat()
    assert raw_redis.get("heartbeat:data") == "ok"
    assert raw_redis.ttl("heartbeat:data") > 0


def test_confidence_threshold_value():
    assert CONFIDENCE_THRESHOLD == 0.65


def test_write_decision_inserts_to_db(db_cursor):
    decision_id = write_decision(
        symbol="AAPL",
        decision="buy",
        confidence=0.9,
        reasoning="RSI oversold, SMA crossover",
        model="claude-haiku",
        acted_on=True,
        skip_reason=None,
    )
    assert isinstance(decision_id, int)
    assert decision_id > 0

    db_cursor.execute("SELECT symbol, decision, confidence, acted_on FROM decisions WHERE id = %s", (decision_id,))
    row = db_cursor.fetchone()
    assert row[0] == "AAPL"
    assert row[1] == "buy"
    assert float(row[2]) == 0.9
    assert row[3] is True


def test_write_decision_hold_not_acted_on(db_cursor):
    decision_id = write_decision(
        symbol="GOOGL",
        decision="hold",
        confidence=0.5,
        reasoning="Insufficient signal",
        model="claude-haiku",
        acted_on=False,
        skip_reason="confidence below threshold",
    )
    db_cursor.execute("SELECT decision, acted_on, skip_reason FROM decisions WHERE id = %s", (decision_id,))
    row = db_cursor.fetchone()
    assert row[0] == "hold"
    assert row[1] is False
    assert row[2] == "confidence below threshold"


def test_write_signal_inserts_to_db_and_redis(db_cursor, raw_redis):
    decision_id = write_decision(
        "AAPL", "buy", 0.85, "Strong momentum", "claude-haiku", True, None
    )
    write_signal("AAPL", "buy", 0.85, decision_id)

    # DB
    db_cursor.execute("SELECT symbol, decision, confidence, decision_id FROM signals WHERE decision_id = %s", (decision_id,))
    row = db_cursor.fetchone()
    assert row[0] == "AAPL"
    assert row[1] == "buy"
    assert float(row[2]) == 0.85
    assert row[3] == decision_id

    # Redis
    messages = raw_redis.xread({"stream:signals": "0"}, count=1)
    assert messages is not None
    _, entries = messages[0]
    _, fields = entries[0]
    signal = json.loads(fields["data"])
    assert signal["symbol"] == "AAPL"
    assert signal["decision"] == "buy"
    assert signal["confidence"] == 0.85
    assert signal["decision_id"] == decision_id
    assert "published_at" in signal


def test_signal_references_decision_in_db(db_cursor):
    """Signals table correctly links back to the originating decision."""
    decision_id = write_decision(
        "MSFT", "sell", 0.8, "Overbought", "claude-sonnet", True, None
    )
    write_signal("MSFT", "sell", 0.8, decision_id)

    db_cursor.execute(
        "SELECT d.symbol, s.decision FROM signals s "
        "JOIN decisions d ON s.decision_id = d.id "
        "WHERE d.id = %s", (decision_id,)
    )
    row = db_cursor.fetchone()
    assert row == ("MSFT", "sell")
