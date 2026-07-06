import json
import pytest
from unittest.mock import patch, MagicMock

from stream_reader import read_next_signals, ack_signal, _ensure_group


# ---------------------------------------------------------------------------
# _ensure_group tests
# ---------------------------------------------------------------------------

def test_ensure_group_creates_consumer_group():
    mock_redis = MagicMock()
    with patch("stream_reader.get_redis", return_value=mock_redis):
        _ensure_group()
    mock_redis.xgroup_create.assert_called_once()
    args, kwargs = mock_redis.xgroup_create.call_args
    assert args[0] == "stream:signals"
    assert args[1] == "execution-group"


def test_ensure_group_ignores_busygroup_error():
    mock_redis = MagicMock()
    mock_redis.xgroup_create.side_effect = Exception("BUSYGROUP Consumer Group name already exists")
    with patch("stream_reader.get_redis", return_value=mock_redis):
        _ensure_group()  # must not raise


def test_ensure_group_reraises_non_busygroup_errors():
    mock_redis = MagicMock()
    mock_redis.xgroup_create.side_effect = Exception("Connection refused")
    with patch("stream_reader.get_redis", return_value=mock_redis):
        with pytest.raises(Exception, match="Connection refused"):
            _ensure_group()


# ---------------------------------------------------------------------------
# read_next_signals tests
# ---------------------------------------------------------------------------

def _make_stream_result(signals: list) -> list:
    messages = [
        (f"1234567890-{i}", {"data": json.dumps(s)})
        for i, s in enumerate(signals)
    ]
    return [("stream:signals", messages)]


def test_read_next_signals_returns_parsed_dicts():
    signal = {"symbol": "AAPL", "decision": "buy", "confidence": 0.78, "decision_id": 42}
    mock_redis = MagicMock()
    mock_redis.xreadgroup.return_value = _make_stream_result([signal])
    with patch("stream_reader.get_redis", return_value=mock_redis):
        results = read_next_signals()
    assert len(results) == 1
    assert results[0]["symbol"] == "AAPL"
    assert results[0]["decision"] == "buy"


def test_read_next_signals_returns_empty_list_when_no_messages():
    mock_redis = MagicMock()
    mock_redis.xreadgroup.return_value = None
    with patch("stream_reader.get_redis", return_value=mock_redis):
        results = read_next_signals()
    assert results == []


def test_read_next_signals_skips_and_acks_malformed_messages():
    mock_redis = MagicMock()
    messages = [
        ("id-0", {"data": json.dumps({"symbol": "AAPL", "decision": "buy"})}),
        ("id-1", {}),                          # missing data field
        ("id-2", {"data": "not-valid-json"}),  # invalid JSON
    ]
    mock_redis.xreadgroup.return_value = [("stream:signals", messages)]
    with patch("stream_reader.get_redis", return_value=mock_redis):
        results = read_next_signals()
    assert len(results) == 1
    assert results[0]["symbol"] == "AAPL"
    assert mock_redis.xack.call_count == 2


def test_read_next_signals_attaches_msg_id():
    signal = {"symbol": "MSFT", "decision": "sell"}
    mock_redis = MagicMock()
    mock_redis.xreadgroup.return_value = _make_stream_result([signal])
    with patch("stream_reader.get_redis", return_value=mock_redis):
        results = read_next_signals()
    assert "_msg_id" in results[0]


# ---------------------------------------------------------------------------
# ack_signal tests
# ---------------------------------------------------------------------------

def test_ack_signal_calls_xack():
    mock_redis = MagicMock()
    with patch("stream_reader.get_redis", return_value=mock_redis):
        ack_signal("1234567890-0")
    mock_redis.xack.assert_called_once_with(
        "stream:signals", "execution-group", "1234567890-0"
    )
