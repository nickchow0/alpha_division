# services/research/tests/test_data.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from datetime import date
from unittest.mock import patch, MagicMock
import pandas as pd

from data import fetch_bars_yfinance, fetch_bars_alpaca

START = date(2023, 1, 1)
END = date(2023, 12, 31)


def _make_yf_df(n: int = 5) -> pd.DataFrame:
    """Minimal yfinance-style DataFrame."""
    idx = pd.date_range("2023-01-03", periods=n, freq="B")
    return pd.DataFrame({
        "Open":   [100.0 + i for i in range(n)],
        "High":   [102.0 + i for i in range(n)],
        "Low":    [ 98.0 + i for i in range(n)],
        "Close":  [101.0 + i for i in range(n)],
        "Volume": [1_000_000 + i * 1000 for i in range(n)],
    }, index=idx)


class TestFetchBarsYfinance(unittest.TestCase):
    @patch("data.yf.Ticker")
    def test_returns_bars_in_expected_format(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_yf_df(5)
        mock_ticker_cls.return_value = mock_ticker

        bars = fetch_bars_yfinance("AAPL", START, END)

        self.assertEqual(len(bars), 5)
        first = bars[0]
        self.assertIn("t", first)
        self.assertIn("o", first)
        self.assertIn("h", first)
        self.assertIn("l", first)
        self.assertIn("c", first)
        self.assertIn("v", first)
        self.assertIsInstance(first["o"], float)
        self.assertIsInstance(first["v"], int)

    @patch("data.yf.Ticker")
    def test_raises_on_empty_response(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        with self.assertRaises(ValueError) as ctx:
            fetch_bars_yfinance("FAKE", START, END)
        self.assertIn("No bars", str(ctx.exception))

    @patch("data.yf.Ticker")
    def test_passes_date_range_to_history(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_yf_df(3)
        mock_ticker_cls.return_value = mock_ticker

        fetch_bars_yfinance("AAPL", START, END)
        call_kwargs = mock_ticker.history.call_args[1]
        self.assertEqual(call_kwargs["start"], START)
        self.assertEqual(call_kwargs["end"], END)


class TestFetchBarsAlpaca(unittest.TestCase):
    @patch("data.tradeapi.REST")
    def test_returns_bars_in_expected_format(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api

        idx = pd.date_range("2023-01-03 09:30", periods=5, freq="15min", tz="UTC")
        mock_df = pd.DataFrame({
            "open":   [100.0 + i for i in range(5)],
            "high":   [102.0 + i for i in range(5)],
            "low":    [ 98.0 + i for i in range(5)],
            "close":  [101.0 + i for i in range(5)],
            "volume": [50000 + i * 100 for i in range(5)],
        }, index=idx)
        mock_api.get_bars.return_value.df = mock_df

        bars = fetch_bars_alpaca(
            symbol="AAPL", start_date=START, end_date=END,
            api_key="key", secret_key="secret", base_url="https://paper-api.alpaca.markets",
        )

        self.assertEqual(len(bars), 5)
        first = bars[0]
        for key in ("t", "o", "h", "l", "c", "v"):
            self.assertIn(key, first)
        self.assertIsInstance(first["o"], float)
        self.assertIsInstance(first["v"], int)

    @patch("data.tradeapi.REST")
    def test_raises_on_empty_response(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api
        mock_api.get_bars.return_value.df = pd.DataFrame()

        with self.assertRaises(ValueError) as ctx:
            fetch_bars_alpaca("FAKE", START, END, "k", "s", "url")
        self.assertIn("No bars", str(ctx.exception))

    @patch("data.tradeapi.REST")
    def test_requests_15min_bars(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api
        idx = pd.date_range("2023-01-03", periods=3, freq="15min", tz="UTC")
        mock_api.get_bars.return_value.df = pd.DataFrame({
            "open": [1.0]*3, "high": [1.0]*3, "low": [1.0]*3,
            "close": [1.0]*3, "volume": [100]*3,
        }, index=idx)

        fetch_bars_alpaca("AAPL", START, END, "k", "s", "url")
        call_args = mock_api.get_bars.call_args
        # Second positional arg is the timeframe
        self.assertIn("15Min", str(call_args))
