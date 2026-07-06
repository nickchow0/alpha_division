import json
import pytest
from unittest.mock import patch, MagicMock

from stream_reader import read_next_snapshots, ack_snapshot, _ensure_group


# ---------------------------------------------------------------------------
# _ensure_group tests
# ---------------------------------------------------------------------------

def test_ensure_group_creates_consumer_group():
    mock_redis = MagicMock()
    with patch("stream_reader.get_redis", return_value=mock_redis):
        _ensure_group()
    mock_redis.xgroup_create.assert_called_once()
    args, kwargs = mock_redis.xgroup_create.call_args
    assert args[0] == "stream:market_snapshot"
    assert args[1] == "analysis-group"


def test_ensure_group_ignores_busygroup_error():
    mock_redis = MagicMock()
    mock_redis.xgroup_create.side_effect = Exception("BUSYGROUP Consumer Group name already exists")
    with patch("stream_reader.get_redis", return_value=mock_redis):
        # Must not raise
        _ensure_group()


def test_ensure_group_reraises_non_busygroup_errors():
    mock_redis = MagicMock()
    mock_redis.xgroup_create.side_effect = Exception("Connection refused")
    with patch("stream_reader.get_redis", return_value=mock_redis):
        with pytest.raises(Exception, match="Connection refused"):
            _ensure_group()


# ---------------------------------------------------------------------------
# read_next_snapshots tests
# ---------------------------------------------------------------------------

def _make_stream_result(snapshots: list) -> list:
    """
    Build the return value that mock_redis.xreadgroup produces.
    Format: [("stream:market_snapshot", [("msg-id-1", {"data": "{...}"}), ...])]
    (decode_responses=True means strings, not bytes)
    """
    messages = [
        (f"1234567890-{i}", {"data": json.dumps(s)})
        for i, s in enumerate(snapshots)
    ]
    return [("stream:market_snapshot", messages)]


def test_read_next_snapshots_returns_parsed_dicts():
    snapshot = {"symbol": "AAPL", "price": 175.0, "rsi": 52.0}
    mock_redis = MagicMock()
    mock_redis.xreadgroup.return_value = _make_stream_result([snapshot])
    with patch("stream_reader.get_redis", return_value=mock_redis):
        results = read_next_snapshots()
    assert len(results) == 1
    assert results[0]["symbol"] == "AAPL"
    assert results[0]["price"] == 175.0


def test_read_next_snapshots_returns_empty_list_when_no_messages():
    mock_redis = MagicMock()
    mock_redis.xreadgroup.return_value = None
    with patch("stream_reader.get_redis", return_value=mock_redis):
        results = read_next_snapshots()
    assert results == []


def test_read_next_snapshots_skips_and_acks_malformed_messages():
    mock_redis = MagicMock()
    # One valid, one missing 'data' field, one invalid JSON
    messages = [
        ("id-0", {"data": json.dumps({"symbol": "AAPL"})}),
        ("id-1", {}),                          # missing data field
        ("id-2", {"data": "not-valid-json"}),  # invalid JSON
    ]
    mock_redis.xreadgroup.return_value = [("stream:market_snapshot", messages)]
    with patch("stream_reader.get_redis", return_value=mock_redis):
        results = read_next_snapshots()
    # Only the valid one is returned
    assert len(results) == 1
    assert results[0]["symbol"] == "AAPL"
    # The two malformed messages were acked so they don't block the group
    assert mock_redis.xack.call_count == 2


def test_read_next_snapshots_attaches_msg_id():
    snapshot = {"symbol": "MSFT", "price": 320.0}
    mock_redis = MagicMock()
    mock_redis.xreadgroup.return_value = _make_stream_result([snapshot])
    with patch("stream_reader.get_redis", return_value=mock_redis):
        results = read_next_snapshots()
    assert "_msg_id" in results[0]


# ---------------------------------------------------------------------------
# ack_snapshot tests
# ---------------------------------------------------------------------------

def test_ack_snapshot_calls_xack():
    mock_redis = MagicMock()
    with patch("stream_reader.get_redis", return_value=mock_redis):
        ack_snapshot("1234567890-0")
    mock_redis.xack.assert_called_once_with(
        "stream:market_snapshot", "analysis-group", "1234567890-0"
    )
