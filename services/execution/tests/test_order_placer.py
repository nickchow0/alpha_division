import pytest
from unittest.mock import patch, MagicMock

from order_placer import write_trade, get_last_buy_price, get_last_short_price, place_order, update_trade_fill, poll_for_fill, reconcile_submitted_trades


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
# get_last_short_price
# ---------------------------------------------------------------------------

def test_get_last_short_price_returns_price_when_found():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (180.00,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = get_last_short_price("AAPL")
    assert result == pytest.approx(180.00)


def test_get_last_short_price_returns_none_when_no_short_found():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = None
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = get_last_short_price("AAPL")
    assert result is None


def test_get_last_short_price_queries_correct_side():
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = None
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        get_last_short_price("TSLA")
    sql, params = mock_cursor.execute.call_args[0]
    assert "short" in sql
    assert "TSLA" in params


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


def test_place_order_short_sends_sell_to_alpaca():
    """Internal side='short' maps to Alpaca side='sell'."""
    mock_order = _make_alpaca_order("order-short-1")
    mock_api = MagicMock()
    mock_api.submit_order.return_value = mock_order
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (2,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = place_order(mock_api, "AAPL", "short", 10, 175.50)
    mock_api.submit_order.assert_called_once_with(
        symbol="AAPL",
        qty=10,
        side="sell",   # Alpaca receives "sell" for a short
        type="market",
        time_in_force="day",
    )
    assert result["side"] == "short"  # DB records our internal side


def test_place_order_cover_sends_buy_to_alpaca():
    """Internal side='cover' maps to Alpaca side='buy'."""
    mock_order = _make_alpaca_order("order-cover-1")
    mock_api = MagicMock()
    mock_api.submit_order.return_value = mock_order
    mock_conn, mock_cursor = _make_mock_conn()
    mock_cursor.fetchone.return_value = (3,)
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        result = place_order(mock_api, "AAPL", "cover", 10, 165.00)
    mock_api.submit_order.assert_called_once_with(
        symbol="AAPL",
        qty=10,
        side="buy",   # Alpaca receives "buy" for a cover
        type="market",
        time_in_force="day",
    )
    assert result["side"] == "cover"  # DB records our internal side


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


# ---------------------------------------------------------------------------
# update_trade_fill
# ---------------------------------------------------------------------------

def test_update_trade_fill_executes_update():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        update_trade_fill(trade_id=7, filled_price=176.30, status="filled")
    mock_cursor.execute.assert_called_once()
    sql, _ = mock_cursor.execute.call_args[0]
    assert "UPDATE trades" in sql


def test_update_trade_fill_sets_correct_params():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        update_trade_fill(trade_id=7, filled_price=176.30, status="filled")
    _, params = mock_cursor.execute.call_args[0]
    # (filled_price, status, status, trade_id) — status appears twice for CASE expression
    assert params == (176.30, "filled", "filled", 7)


def test_update_trade_fill_sets_filled_at_in_sql():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        update_trade_fill(trade_id=7, filled_price=176.30, status="filled")
    sql, _ = mock_cursor.execute.call_args[0]
    assert "filled_at" in sql


def test_update_trade_fill_accepts_failed_status():
    mock_conn, mock_cursor = _make_mock_conn()
    with patch("order_placer.get_conn", return_value=_make_mock_cm(mock_conn)):
        update_trade_fill(trade_id=3, filled_price=None, status="failed")
    _, params = mock_cursor.execute.call_args[0]
    # (filled_price, status, status, trade_id)
    assert params == (None, "failed", "failed", 3)


# ---------------------------------------------------------------------------
# poll_for_fill
# ---------------------------------------------------------------------------

def _make_alpaca_order_status(status: str, filled_avg_price=None):
    order = MagicMock()
    order.status = status
    order.filled_avg_price = filled_avg_price
    return order


def test_poll_for_fill_returns_fill_price_when_filled_immediately():
    mock_api = MagicMock()
    mock_api.get_order.return_value = _make_alpaca_order_status("filled", filled_avg_price="176.30")
    result = poll_for_fill(mock_api, "order-123", timeout_seconds=5, poll_interval=0.1)
    assert result == ("filled", pytest.approx(176.30))


def test_poll_for_fill_returns_none_price_on_terminal_non_fill():
    mock_api = MagicMock()
    mock_api.get_order.return_value = _make_alpaca_order_status("canceled")
    result = poll_for_fill(mock_api, "order-123", timeout_seconds=5, poll_interval=0.1)
    assert result == ("canceled", None)


def test_poll_for_fill_retries_until_filled():
    mock_api = MagicMock()
    mock_api.get_order.side_effect = [
        _make_alpaca_order_status("new"),
        _make_alpaca_order_status("new"),
        _make_alpaca_order_status("filled", filled_avg_price="180.00"),
    ]
    status, price = poll_for_fill(mock_api, "order-123", timeout_seconds=5, poll_interval=0.01)
    assert status == "filled"
    assert price == pytest.approx(180.00)
    assert mock_api.get_order.call_count == 3


def test_poll_for_fill_times_out_and_returns_submitted():
    mock_api = MagicMock()
    mock_api.get_order.return_value = _make_alpaca_order_status("new")
    status, price = poll_for_fill(mock_api, "order-123", timeout_seconds=0.05, poll_interval=0.01)
    assert status == "submitted"
    assert price is None


def test_poll_for_fill_handles_expired_status():
    mock_api = MagicMock()
    mock_api.get_order.return_value = _make_alpaca_order_status("expired")
    status, price = poll_for_fill(mock_api, "order-123", timeout_seconds=5, poll_interval=0.1)
    assert status == "expired"
    assert price is None


# ---------------------------------------------------------------------------
# reconcile_submitted_trades tests
# ---------------------------------------------------------------------------

def _make_pending_rows(*rows):
    """Simulate DB returning list of (trade_id, alpaca_order_id, symbol) tuples."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = list(rows)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


def test_reconcile_updates_filled_trade():
    mock_cm = _make_pending_rows((42, "alpaca-abc", "AAPL"))
    mock_api = MagicMock()
    mock_api.get_order.return_value = _make_alpaca_order_status("filled", filled_avg_price="182.50")

    with patch("order_placer.get_conn", return_value=mock_cm), \
         patch("order_placer.update_trade_fill") as mock_update:
        count = reconcile_submitted_trades(mock_api)

    assert count == 1
    mock_update.assert_called_once_with(42, 182.50, "filled")


def test_reconcile_updates_canceled_trade():
    mock_cm = _make_pending_rows((7, "alpaca-xyz", "MSFT"))
    mock_api = MagicMock()
    mock_api.get_order.return_value = _make_alpaca_order_status("canceled")

    with patch("order_placer.get_conn", return_value=mock_cm), \
         patch("order_placer.update_trade_fill") as mock_update:
        count = reconcile_submitted_trades(mock_api)

    assert count == 1
    mock_update.assert_called_once_with(7, None, "canceled")


def test_reconcile_skips_still_pending_trade():
    mock_cm = _make_pending_rows((3, "alpaca-pending", "GOOGL"))
    mock_api = MagicMock()
    mock_api.get_order.return_value = _make_alpaca_order_status("new")

    with patch("order_placer.get_conn", return_value=mock_cm), \
         patch("order_placer.update_trade_fill") as mock_update:
        count = reconcile_submitted_trades(mock_api)

    assert count == 0
    mock_update.assert_not_called()


def test_reconcile_returns_zero_when_no_submitted_trades():
    mock_cm = _make_pending_rows()  # empty
    mock_api = MagicMock()

    with patch("order_placer.get_conn", return_value=mock_cm):
        count = reconcile_submitted_trades(mock_api)

    assert count == 0
    mock_api.get_order.assert_not_called()


def test_reconcile_handles_alpaca_error_gracefully():
    mock_cm = _make_pending_rows((5, "bad-order-id", "TSLA"))
    mock_api = MagicMock()
    mock_api.get_order.side_effect = Exception("Order not found")

    with patch("order_placer.get_conn", return_value=mock_cm), \
         patch("order_placer.update_trade_fill") as mock_update:
        count = reconcile_submitted_trades(mock_api)

    assert count == 0
    mock_update.assert_not_called()


def test_reconcile_handles_multiple_trades():
    mock_cm = _make_pending_rows(
        (1, "ord-filled", "AAPL"),
        (2, "ord-canceled", "MSFT"),
        (3, "ord-pending", "GOOG"),
    )
    def get_order_side_effect(order_id):
        if order_id == "ord-filled":
            return _make_alpaca_order_status("filled", filled_avg_price="150.00")
        if order_id == "ord-canceled":
            return _make_alpaca_order_status("canceled")
        return _make_alpaca_order_status("new")

    mock_api = MagicMock()
    mock_api.get_order.side_effect = get_order_side_effect

    with patch("order_placer.get_conn", return_value=mock_cm), \
         patch("order_placer.update_trade_fill") as mock_update:
        count = reconcile_submitted_trades(mock_api)

    assert count == 2
    assert mock_update.call_count == 2
