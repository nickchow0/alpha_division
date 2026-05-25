"""Unit tests for collector.py — all external I/O is mocked."""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from collector import collect_bars, _fetch_alpaca


def _make_alpaca_df(n=10, start="2024-01-01"):
    """Build a minimal OHLCV DataFrame mimicking Alpaca daily bars output."""
    idx = pd.date_range(start=start, periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "open":   [100.0 + i for i in range(n)],
            "high":   [102.0 + i for i in range(n)],
            "low":    [98.0 + i for i in range(n)],
            "close":  [101.0 + i for i in range(n)],
            "volume": [1_000_000 + i * 1000 for i in range(n)],
        },
        index=idx,
    )


def _mock_client(df):
    """Return a mock Alpaca REST client whose get_bars().df returns df."""
    mock_api = MagicMock()
    mock_resp = MagicMock()
    mock_resp.df = df
    mock_api.get_bars.return_value = mock_resp
    return mock_api


def test_fetch_alpaca_returns_list_of_dicts():
    df = _make_alpaca_df(5)
    with patch("collector._alpaca_client", return_value=_mock_client(df)):
        bars = _fetch_alpaca("AAPL", date(2024, 1, 1), date(2024, 1, 10))
    assert len(bars) == 5
    assert "date" in bars[0]
    assert "open" in bars[0]
    assert "close" in bars[0]
    assert "volume" in bars[0]


def test_fetch_alpaca_empty_returns_empty():
    with patch("collector._alpaca_client", return_value=_mock_client(pd.DataFrame())):
        bars = _fetch_alpaca("AAPL", date(2024, 1, 1), date(2024, 1, 2))
    assert bars == []


def test_fetch_alpaca_returns_empty_on_exception():
    mock_api = MagicMock()
    mock_api.get_bars.side_effect = Exception("connection error")
    with patch("collector._alpaca_client", return_value=mock_api):
        bars = _fetch_alpaca("AAPL", date(2024, 1, 1), date(2024, 1, 10))
    assert bars == []


def test_collect_bars_uses_cache():
    """collect_bars returns DB-cached bars without calling Alpaca for covered dates."""
    today = date.today()
    cached_bars = [
        {"date": today - timedelta(days=i), "open": 100.0, "high": 102.0,
         "low": 98.0, "close": 101.0, "volume": 1_000_000}
        for i in range(400, 0, -1)
    ]

    with patch("collector.get_cached_bars", return_value=cached_bars), \
         patch("collector.save_bars") as mock_save, \
         patch("collector._alpaca_client") as mock_client:
        result = collect_bars(["AAPL"], lookback_days=365)

    mock_client.assert_not_called()  # full cache hit → no Alpaca call
    assert "AAPL" in result
    assert len(result["AAPL"]) == 400


def test_collect_bars_fetches_missing_dates():
    """collect_bars fetches only the gap between cached data and today."""
    today = date.today()
    cached_bars = [
        {"date": today - timedelta(days=i), "open": 100.0, "high": 102.0,
         "low": 98.0, "close": 101.0, "volume": 1_000_000}
        for i in range(20, 10, -1)
    ]
    new_df = _make_alpaca_df(10)

    with patch("collector.get_cached_bars", return_value=cached_bars), \
         patch("collector.save_bars") as mock_save, \
         patch("collector._alpaca_client", return_value=_mock_client(new_df)):
        result = collect_bars(["AAPL"], lookback_days=30)

    mock_save.assert_called_once()


def test_collect_bars_parallel_handles_multiple_symbols():
    new_df = _make_alpaca_df(5)
    with patch("collector.get_cached_bars", return_value=[]), \
         patch("collector.save_bars"), \
         patch("collector._alpaca_client", return_value=_mock_client(new_df)):
        result = collect_bars(["AAPL", "MSFT"], lookback_days=30)
    assert "AAPL" in result
    assert "MSFT" in result


def test_collect_bars_omits_symbol_on_exception():
    """collect_bars silently drops a symbol when Alpaca raises an exception."""
    mock_api = MagicMock()
    mock_api.get_bars.side_effect = Exception("API error")
    with patch("collector.get_cached_bars", return_value=[]), \
         patch("collector.save_bars"), \
         patch("collector._alpaca_client", return_value=mock_api):
        result = collect_bars(["SITM"], lookback_days=30)
    assert result == {}
