from typing import Optional

from shared.db import get_conn

# get_conn() auto-commits on context exit (see shared/db.py). No explicit
# conn.commit() needed; rollback happens automatically on exception.


def write_trade(
    symbol: str,
    side: str,
    qty: int,
    price: Optional[float],
    alpaca_order_id: str,
    signal_id: Optional[int],
    status: str,
    confidence: Optional[float] = None,
    quoted_price: Optional[float] = None,
) -> int:
    """
    Insert a trade record into the trades table. Returns the new row ID.

    Parameters:
        symbol: ticker symbol (e.g. "AAPL")
        side: "buy" or "sell"
        qty: number of shares
        price: last bar close at submission (used for position sizing)
        alpaca_order_id: the order ID returned by Alpaca
        signal_id: optional reference to the signals table row
        status: "submitted" on initial write; can be updated to "filled" or "failed"
        confidence: AI confidence score copied from the signal (0.0–1.0)
        quoted_price: ask (buy) or bid (sell) from quote API at submission;
            used with price to compute slippage; None for pre-slippage-tracking trades
    """
    sql = """
        INSERT INTO trades (symbol, side, qty, price, quoted_price, alpaca_order_id, signal_id, status, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, side, qty, price, quoted_price, alpaca_order_id, signal_id, status, confidence))
            return cur.fetchone()[0]


def get_last_buy_price(symbol: str) -> Optional[float]:
    """
    Returns the price from the most recent non-failed buy trade for this symbol.

    Used to estimate realized P&L when a sell order is placed:
        realized_pnl = (sell_price - buy_price) * qty

    Returns None if no buy trade exists for this symbol.
    """
    sql = """
        SELECT price FROM trades
        WHERE symbol = %s AND side = 'buy' AND status != 'failed'
        ORDER BY placed_at DESC
        LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol,))
            row = cur.fetchone()
    return float(row[0]) if row else None


def place_order(
    api,
    symbol: str,
    side: str,
    qty: int,
    estimated_price: float,
    signal_id: Optional[int] = None,
    confidence: Optional[float] = None,
    quoted_price: Optional[float] = None,
) -> dict:
    """
    Submit a market order to Alpaca and record it in the trades table.

    Parameters:
        api: Alpaca REST client
        symbol: ticker symbol
        side: "buy" or "sell"
        qty: number of shares to trade
        estimated_price: last bar close price (for sizing reference and P&L)
        signal_id: optional reference to the signals table
        confidence: AI confidence score from the signal (0.0–1.0)
        quoted_price: ask (buy) or bid (sell) from quote API immediately before
            order submission; enables slippage tracking; None if unavailable

    Returns a dict with: id, symbol, side, qty, price, alpaca_order_id, status.

    Raises any exception from api.submit_order or write_trade — the caller
    is responsible for catching and logging.
    """
    order = api.submit_order(
        symbol=symbol,
        qty=qty,
        side=side,
        type="market",
        time_in_force="day",
    )

    trade_id = write_trade(
        symbol=symbol,
        side=side,
        qty=qty,
        price=estimated_price,
        alpaca_order_id=str(order.id),
        signal_id=signal_id,
        status="submitted",
        confidence=confidence,
        quoted_price=quoted_price,
    )

    return {
        "id": trade_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": estimated_price,
        "alpaca_order_id": str(order.id),
        "status": "submitted",
    }
