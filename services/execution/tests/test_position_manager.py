import pytest
import pandas as pd
from unittest.mock import MagicMock

from position_manager import get_positions, get_portfolio_value, get_last_price, get_quote


def _make_mock_position(symbol: str, qty: str) -> MagicMock:
    pos = MagicMock()
    pos.symbol = symbol
    pos.qty = qty
    return pos


def _make_mock_api_with_positions(positions: list) -> MagicMock:
    api = MagicMock()
    api.list_positions.return_value = positions
    return api


def _make_mock_api_with_equity(equity: str) -> MagicMock:
    api = MagicMock()
    account = MagicMock()
    account.equity = equity
    api.get_account.return_value = account
    return api


def _make_mock_api_with_price(symbol: str, close_price: float) -> MagicMock:
    index = pd.date_range("2026-05-15 14:30", periods=1, freq="1min", tz="UTC")
    df = pd.DataFrame({"close": [close_price]}, index=index)
    bars_resp = MagicMock()
    bars_resp.df = df
    api = MagicMock()
    api.get_bars.return_value = bars_resp
    return api


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------

def test_get_positions_returns_symbol_qty_dict():
    positions = [
        _make_mock_position("AAPL", "5"),
        _make_mock_position("MSFT", "10"),
    ]
    api = _make_mock_api_with_positions(positions)
    result = get_positions(api)
    assert result == {"AAPL": 5, "MSFT": 10}


def test_get_positions_returns_empty_dict_when_no_positions():
    api = _make_mock_api_with_positions([])
    result = get_positions(api)
    assert result == {}


def test_get_positions_casts_qty_to_int():
    positions = [_make_mock_position("AAPL", "3")]
    api = _make_mock_api_with_positions(positions)
    result = get_positions(api)
    assert isinstance(result["AAPL"], int)
    assert result["AAPL"] == 3


def test_get_positions_calls_list_positions():
    api = _make_mock_api_with_positions([])
    get_positions(api)
    api.list_positions.assert_called_once()


# ---------------------------------------------------------------------------
# get_portfolio_value
# ---------------------------------------------------------------------------

def test_get_portfolio_value_returns_float():
    api = _make_mock_api_with_equity("125432.87")
    result = get_portfolio_value(api)
    assert result == pytest.approx(125432.87)
    assert isinstance(result, float)


def test_get_portfolio_value_calls_get_account():
    api = _make_mock_api_with_equity("50000.00")
    get_portfolio_value(api)
    api.get_account.assert_called_once()


# ---------------------------------------------------------------------------
# get_last_price
# ---------------------------------------------------------------------------

def test_get_last_price_returns_close_price():
    api = _make_mock_api_with_price("AAPL", 175.50)
    result = get_last_price(api, "AAPL")
    assert result == pytest.approx(175.50)


def test_get_last_price_raises_on_empty_dataframe():
    bars_resp = MagicMock()
    bars_resp.df = pd.DataFrame()
    api = MagicMock()
    api.get_bars.return_value = bars_resp
    with pytest.raises(ValueError, match="AAPL"):
        get_last_price(api, "AAPL")


def test_get_last_price_calls_get_bars_with_symbol():
    api = _make_mock_api_with_price("MSFT", 320.0)
    get_last_price(api, "MSFT")
    call_args = api.get_bars.call_args[0]
    assert call_args[0] == "MSFT"


# ---------------------------------------------------------------------------
# get_quote
# ---------------------------------------------------------------------------

def _make_mock_api_with_quote(ask: float, bid: float) -> MagicMock:
    quote = MagicMock()
    quote.ap = ask
    quote.bp = bid
    api = MagicMock()
    api.get_latest_quote.return_value = quote
    return api


def test_get_quote_returns_ask_and_bid():
    api = _make_mock_api_with_quote(175.60, 175.50)
    ask, bid = get_quote(api, "AAPL")
    assert ask == pytest.approx(175.60)
    assert bid == pytest.approx(175.50)


def test_get_quote_returns_floats():
    api = _make_mock_api_with_quote(320.10, 320.05)
    ask, bid = get_quote(api, "MSFT")
    assert isinstance(ask, float)
    assert isinstance(bid, float)


def test_get_quote_calls_get_latest_quote_with_symbol():
    api = _make_mock_api_with_quote(100.0, 99.9)
    get_quote(api, "GOOGL")
    api.get_latest_quote.assert_called_once_with("GOOGL")
