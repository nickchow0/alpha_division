import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock, call

from pnl_tracker import get_today_pnl, add_realized_pnl, is_circuit_breaker_triggered, trigger_circuit_breaker


# ---------------------------------------------------------------------------
# Helpers (same pattern used throughout this project)
# ---------------------------------------------------------------------------

def _make_mock_conn():
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


def _make_mock_cm(mock_conn):
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


_TODAY = date(2026, 5, 15)


# ---------------------------------------------------------------------------
# get_today_pnl
# ---------------------------------------------------------------------------

def test_get_today_pnl_returns_value_from_db():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (Decimal("-150.00"),)
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = get_today_pnl(_TODAY)
    assert result == pytest.approx(-150.0)


def test_get_today_pnl_returns_zero_when_no_record():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = None
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = get_today_pnl(_TODAY)
    assert result == 0.0


def test_get_today_pnl_queries_by_date():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (Decimal("0"),)
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        get_today_pnl(_TODAY)
    _, params = mock_cursor.execute.call_args[0]
    assert params == (_TODAY,)


def test_get_today_pnl_returns_positive_when_profitable():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (Decimal("87.50"),)
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = get_today_pnl(_TODAY)
    assert result == pytest.approx(87.50)


# ---------------------------------------------------------------------------
# add_realized_pnl
# ---------------------------------------------------------------------------

def test_add_realized_pnl_executes_upsert():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        add_realized_pnl(-50.0, _TODAY)
    mock_cursor.execute.assert_called_once()
    sql, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO daily_pnl" in sql
    assert "ON CONFLICT" in sql
    assert params == (_TODAY, -50.0)


def test_add_realized_pnl_uses_upsert_not_plain_insert():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        add_realized_pnl(100.0, _TODAY)
    sql, _ = mock_cursor.execute.call_args[0]
    assert "ON CONFLICT" in sql
    assert "realized_pnl" in sql


# ---------------------------------------------------------------------------
# is_circuit_breaker_triggered
# ---------------------------------------------------------------------------

def test_is_circuit_breaker_triggered_returns_true_when_set():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (True,)
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = is_circuit_breaker_triggered(_TODAY)
    assert result is True


def test_is_circuit_breaker_triggered_returns_false_when_not_set():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (False,)
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = is_circuit_breaker_triggered(_TODAY)
    assert result is False


def test_is_circuit_breaker_triggered_returns_false_when_no_record():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = None
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = is_circuit_breaker_triggered(_TODAY)
    assert result is False


# ---------------------------------------------------------------------------
# trigger_circuit_breaker
# ---------------------------------------------------------------------------

def test_trigger_circuit_breaker_executes_upsert():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("pnl_tracker.get_conn", return_value=_make_mock_cm(mock_conn)):
        trigger_circuit_breaker(_TODAY)
    mock_cursor.execute.assert_called_once()
    sql, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO daily_pnl" in sql
    assert "circuit_breaker_triggered" in sql
    assert "ON CONFLICT" in sql
    assert params == (_TODAY,)
