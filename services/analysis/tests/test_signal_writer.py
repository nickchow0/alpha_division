import json
import pytest
from unittest.mock import patch, MagicMock

from signal_writer import write_decision, write_signal, CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_conn():
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (42,)  # simulated RETURNING id
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


def _make_mock_cm(mock_conn):
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


# ---------------------------------------------------------------------------
# write_decision tests
# ---------------------------------------------------------------------------

def test_write_decision_executes_insert():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("signal_writer.get_conn", return_value=_make_mock_cm(mock_conn)):
        write_decision("AAPL", "buy", 0.78, "Strong setup.", "claude-haiku-4-5", True, None)
    mock_cursor.execute.assert_called_once()
    sql, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO decisions" in sql


def test_write_decision_params_are_correct():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("signal_writer.get_conn", return_value=_make_mock_cm(mock_conn)):
        write_decision("MSFT", "hold", 0.55, "Neutral.", "claude-haiku-4-5", False, "confidence below threshold")
    _, params = mock_cursor.execute.call_args[0]
    assert params == ("MSFT", "hold", 0.55, "Neutral.", "claude-haiku-4-5", False, "confidence below threshold")


def test_write_decision_returns_integer_id():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (99,)
    with patch("signal_writer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = write_decision("AAPL", "buy", 0.78, "Good.", "claude-haiku-4-5", True, None)
    assert result == 99
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# write_signal tests
# ---------------------------------------------------------------------------

def test_write_signal_inserts_into_signals_table():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_redis = MagicMock()
    with patch("signal_writer.get_conn", return_value=_make_mock_cm(mock_conn)), \
         patch("signal_writer.get_redis", return_value=mock_redis):
        write_signal("AAPL", "buy", 0.78, decision_id=42)
    mock_cursor.execute.assert_called_once()
    sql, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO signals" in sql
    assert params == ("AAPL", "buy", 0.78, 42)


def test_write_signal_calls_xadd():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_redis = MagicMock()
    with patch("signal_writer.get_conn", return_value=_make_mock_cm(mock_conn)), \
         patch("signal_writer.get_redis", return_value=mock_redis):
        write_signal("AAPL", "buy", 0.78, decision_id=42)
    mock_redis.xadd.assert_called_once()


def test_write_signal_xadd_uses_correct_stream_key():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_redis = MagicMock()
    with patch("signal_writer.get_conn", return_value=_make_mock_cm(mock_conn)), \
         patch("signal_writer.get_redis", return_value=mock_redis):
        write_signal("AAPL", "buy", 0.78, decision_id=42)
    stream_key = mock_redis.xadd.call_args[0][0]
    assert stream_key == "stream:signals"


def test_write_signal_xadd_data_is_valid_json():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_redis = MagicMock()
    with patch("signal_writer.get_conn", return_value=_make_mock_cm(mock_conn)), \
         patch("signal_writer.get_redis", return_value=mock_redis):
        write_signal("AAPL", "buy", 0.78, decision_id=42)
    fields = mock_redis.xadd.call_args[0][1]
    parsed = json.loads(fields["data"])
    assert isinstance(parsed, dict)
    for key in ("symbol", "decision", "confidence", "decision_id", "published_at"):
        assert key in parsed, f"Missing key '{key}' in signal JSON"


def test_write_signal_caps_stream_at_1000():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_redis = MagicMock()
    with patch("signal_writer.get_conn", return_value=_make_mock_cm(mock_conn)), \
         patch("signal_writer.get_redis", return_value=mock_redis):
        write_signal("AAPL", "buy", 0.78, decision_id=42)
    assert mock_redis.xadd.call_args[1]["maxlen"] == 1000


def test_confidence_threshold_is_correct_value():
    assert CONFIDENCE_THRESHOLD == 0.65
