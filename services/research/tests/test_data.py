# services/research/tests/test_data.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from datetime import date
from unittest.mock import patch, MagicMock
import pandas as pd

from data import fetch_bars_alpaca

START = date(2023, 1, 1)
END = date(2023, 12, 31)


def _make_alpaca_df(n: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2023-01-03", periods=n, freq="B", tz="UTC")
    return pd.DataFrame({
        "open":   [100.0 + i for i in range(n)],
        "high":   [102.0 + i for i in range(n)],
        "low":    [ 98.0 + i for i in range(n)],
        "close":  [101.0 + i for i in range(n)],
        "volume": [1_000_000 + i * 1000 for i in range(n)],
    }, index=idx)


class TestFetchBarsAlpaca(unittest.TestCase):
    @patch("data.tradeapi.REST")
    def test_returns_bars_in_expected_format(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api
        mock_api.get_bars.return_value.df = _make_alpaca_df(5)

        bars = fetch_bars_alpaca(
            symbol="AAPL", start_date=START, end_date=END,
            api_key="key", secret_key="secret",
            base_url="https://paper-api.alpaca.markets",
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
    def test_requests_daily_bars(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api
        mock_api.get_bars.return_value.df = _make_alpaca_df(3)

        fetch_bars_alpaca("AAPL", START, END, "k", "s", "url")

        call_args = mock_api.get_bars.call_args
        self.assertIn("1Day", str(call_args))

    @patch("data.tradeapi.REST")
    def test_passes_date_range(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api
        mock_api.get_bars.return_value.df = _make_alpaca_df(3)

        fetch_bars_alpaca("AAPL", START, END, "k", "s", "url")

        call_kwargs = mock_api.get_bars.call_args[1]
        self.assertEqual(call_kwargs["start"], START.isoformat())
        self.assertEqual(call_kwargs["end"], END.isoformat())
