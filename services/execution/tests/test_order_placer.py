import pytest
from unittest.mock import patch, MagicMock

from order_placer import write_trade, get_last_buy_price, place_order


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


def _make_alpaca_order(order_id: str = "test-order-123") -> MagicMock:
    order = MagicMock()
    order.id = order_id
    return order


# ---------------------------------------------------------------------------
# write_trade
# ---------------------------------------------------------------------------

def test_write_trade_executes_insert():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (1,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        write_trade("AAPL", "buy", 10, 175.50, "order-123", None, "submitted")
    mock_cursor.execute.assert_called_once()
    sql, _ = mock_cursor.execute.call_args[0]
    assert "INSERT INTO trades" in sql


def test_write_trade_params_are_correct():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (5,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        write_trade("MSFT", "sell", 3, 320.00, "order-456", None, "submitted", confidence=0.85, quoted_price=319.90)
    _, params = mock_cursor.execute.call_args[0]
    assert params == ("MSFT", "sell", 3, 320.00, 319.90, "order-456", None, "submitted", 0.85)


def test_write_trade_quoted_price_defaults_to_none():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (5,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        write_trade("AAPL", "buy", 5, 175.00, "order-789", None, "submitted")
    _, params = mock_cursor.execute.call_args[0]
    # quoted_price is the 5th element (index 4)
    assert params[4] is None


def test_write_trade_confidence_defaults_to_none():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (5,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        write_trade("MSFT", "sell", 3, 320.00, "order-456", None, "submitted")
    _, params = mock_cursor.execute.call_args[0]
    assert params[-1] is None


def test_write_trade_returns_integer_id():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (42,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = write_trade("AAPL", "buy", 5, 175.0, "order-789", None, "submitted")
    assert result == 42
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# get_last_buy_price
# ---------------------------------------------------------------------------

def test_get_last_buy_price_returns_price_when_found():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (150.25,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = get_last_buy_price("AAPL")
    assert result == pytest.approx(150.25)


def test_get_last_buy_price_returns_none_when_no_buy_found():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = None
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = get_last_buy_price("AAPL")
    assert result is None


def test_get_last_buy_price_queries_by_symbol():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = None
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        get_last_buy_price("MSFT")
    _, params = mock_cursor.execute.call_args[0]
    assert "MSFT" in params


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------

def test_place_order_submits_market_order_to_alpaca():
    mock_order = _make_alpaca_order("order-abc")
    mock_api = MagicMock()
    mock_api.submit_order.return_value = mock_order
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (1,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        place_order(mock_api, "AAPL", "buy", 10, 175.50)
    mock_api.submit_order.assert_called_once_with(
        symbol="AAPL",
        qty=10,
        side="buy",
        type="market",
        time_in_force="day",
    )


def test_place_order_writes_to_trades_table():
    mock_order = _make_alpaca_order("order-xyz")
    mock_api = MagicMock()
    mock_api.submit_order.return_value = mock_order
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (7,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        place_order(mock_api, "MSFT", "sell", 5, 320.0)
    sql, params = mock_cursor.execute.call_args[0]
    assert "INSERT INTO trades" in sql
    assert "MSFT" in params


def test_place_order_returns_trade_dict():
    mock_order = _make_alpaca_order("order-ret")
    mock_api = MagicMock()
    mock_api.submit_order.return_value = mock_order
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (99,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = place_order(mock_api, "AAPL", "buy", 10, 175.50)
    assert result["id"] == 99
    assert result["symbol"] == "AAPL"
    assert result["side"] == "buy"
    assert result["qty"] == 10
    assert result["alpaca_order_id"] == "order-ret"
    assert result["status"] == "submitted"


def test_place_order_uses_status_submitted():
    mock_order = _make_alpaca_order()
    mock_api = MagicMock()
    mock_api.submit_order.return_value = mock_order
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (1,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = place_order(mock_api, "AAPL", "buy", 5, 175.0)
    assert result["status"] == "submitted"
    _, params = mock_cursor.execute.call_args[0]
    assert "submitted" in params
