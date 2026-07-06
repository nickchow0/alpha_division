import pytest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from market import is_market_open, get_watchlist

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# is_market_open() tests
# ---------------------------------------------------------------------------

def test_market_open_during_trading_hours():
    # Wednesday 10:00 AM ET — should be open
    fake_now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=ET)
    with patch("market.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        assert is_market_open() is True
        mock_dt.now.assert_called_with(ET)


def test_market_closed_before_open():
    # Wednesday 9:00 AM ET — before 9:30am
    fake_now = datetime(2026, 5, 13, 9, 0, 0, tzinfo=ET)
    with patch("market.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        assert is_market_open() is False
        mock_dt.now.assert_called_with(ET)


def test_market_closed_after_close():
    # Wednesday 4:30 PM ET — after 4:00pm
    fake_now = datetime(2026, 5, 13, 16, 30, 0, tzinfo=ET)
    with patch("market.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        assert is_market_open() is False
        mock_dt.now.assert_called_with(ET)


def test_market_closed_on_saturday():
    # Saturday 12:00 PM ET
    fake_now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=ET)
    with patch("market.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        assert is_market_open() is False
        mock_dt.now.assert_called_with(ET)


def test_market_closed_on_sunday():
    # Sunday 12:00 PM ET
    fake_now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=ET)
    with patch("market.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        assert is_market_open() is False
        mock_dt.now.assert_called_with(ET)


def test_market_open_at_exactly_930():
    # Wednesday exactly 9:30 AM ET — boundary: should be open
    fake_now = datetime(2026, 5, 13, 9, 30, 0, tzinfo=ET)
    with patch("market.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        assert is_market_open() is True
        mock_dt.now.assert_called_with(ET)


def test_market_closed_at_exactly_4pm():
    # Wednesday exactly 4:00 PM ET — boundary: should be closed (market closes at 4pm)
    fake_now = datetime(2026, 5, 13, 16, 0, 0, tzinfo=ET)
    with patch("market.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        assert is_market_open() is False
        mock_dt.now.assert_called_with(ET)


# ---------------------------------------------------------------------------
# get_watchlist() tests
# ---------------------------------------------------------------------------

def test_get_watchlist_returns_symbols_from_config():
    with patch("market.load_config", return_value={"watchlist": ["TSLA", "NVDA", "AMD"]}):
        result = get_watchlist()
    assert result == ["TSLA", "NVDA", "AMD"]


def test_get_watchlist_falls_back_to_default_when_key_missing():
    with patch("market.load_config", return_value={}):
        result = get_watchlist()
    assert result == ["AAPL", "MSFT", "GOOGL"]


def test_get_watchlist_returns_single_symbol():
    with patch("market.load_config", return_value={"watchlist": ["AMZN"]}):
        result = get_watchlist()
    assert result == ["AMZN"]


def test_get_watchlist_preserves_order():
    symbols = ["GOOGL", "AAPL", "MSFT", "NVDA"]
    with patch("market.load_config", return_value={"watchlist": symbols}):
        result = get_watchlist()
    assert result == symbols


def test_get_watchlist_delegates_to_load_config():
    """Ensure get_watchlist always reads from config (not a stale cache)."""
    with patch("market.load_config", return_value={"watchlist": ["AAPL"]}) as mock_cfg:
        get_watchlist()
    mock_cfg.assert_called_once()
