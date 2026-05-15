import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from health_checker import write_health_result, check_all


# ---------------------------------------------------------------------------
# Helpers
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


def _sample_bars_df():
    index = pd.date_range("2026-01-01", periods=1, freq="D", tz="UTC")
    return pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [1_000_000]},
        index=index,
    )


def _make_alpaca_api(df):
    mock_bars_resp = MagicMock()
    mock_bars_resp.df = df
    mock_api = MagicMock()
    mock_api.get_bars.return_value = mock_bars_resp
    return mock_api


def _make_requests_ok_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = [{"headline": "test", "datetime": 1715000000}]
    return mock_resp


def _make_fred_ok_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"observations": [{"date": "2026-01-01", "value": "5.33"}]}
    return mock_resp


# ---------------------------------------------------------------------------
# write_health_result tests
# ---------------------------------------------------------------------------

def test_write_health_result_executes_insert():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cm = _make_mock_cm(mock_conn)

    with patch("health_checker.get_conn", return_value=mock_cm):
        write_health_result("alpaca", "ok", 150, None)

    mock_cursor.execute.assert_called_once()
    call_args = mock_cursor.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "INSERT INTO api_health" in sql
    assert "alpaca" in params
    assert "ok" in params


def test_write_health_result_includes_latency():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cm = _make_mock_cm(mock_conn)

    with patch("health_checker.get_conn", return_value=mock_cm):
        write_health_result("alpaca", "ok", 200, None)

    call_args = mock_cursor.execute.call_args
    params = call_args[0][1]
    assert 200 in params


def test_write_health_result_includes_error_message():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cm = _make_mock_cm(mock_conn)

    with patch("health_checker.get_conn", return_value=mock_cm):
        write_health_result("alpaca", "error", 0, "Connection refused")

    call_args = mock_cursor.execute.call_args
    params = call_args[0][1]
    assert "Connection refused" in params


# ---------------------------------------------------------------------------
# check_all tests
# ---------------------------------------------------------------------------

def test_check_all_returns_ok_when_all_succeed():
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets", "ftoken", "fredkey")

    assert result["alpaca"] == "ok"
    assert result["finnhub"] == "ok"
    assert result["fred"] == "ok"


def test_check_all_returns_error_for_alpaca_on_exception():
    mock_api = MagicMock()
    mock_api.get_bars.side_effect = Exception("down")
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets", "ftoken", "fredkey")

    assert result["alpaca"] == "error"


def test_check_all_returns_warning_for_finnhub_on_exception():
    mock_api = _make_alpaca_api(_sample_bars_df())
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[Exception("finnhub down"), fred_resp]), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets", "ftoken", "fredkey")

    assert result["finnhub"] == "warning"


def test_check_all_returns_warning_for_fred_on_exception():
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, Exception("fred down")]), \
         patch("health_checker.write_health_result"):
        result = check_all("key", "secret", "https://paper-api.alpaca.markets", "ftoken", "fredkey")

    assert result["fred"] == "warning"


def test_check_all_calls_write_health_result_three_times():
    mock_api = _make_alpaca_api(_sample_bars_df())
    finnhub_resp = _make_requests_ok_response()
    fred_resp = _make_fred_ok_response()

    with patch("health_checker.tradeapi.REST", return_value=mock_api), \
         patch("health_checker.requests.get", side_effect=[finnhub_resp, fred_resp]), \
         patch("health_checker.write_health_result") as mock_write:
        check_all("key", "secret", "https://paper-api.alpaca.markets", "ftoken", "fredkey")

    assert mock_write.call_count == 3
