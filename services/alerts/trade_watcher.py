import psycopg2.extras

from shared.db import get_conn
from shared.redis_client import get_redis
from shared.logger import get_logger

log = get_logger("trade_watcher")

_LAST_TRADE_KEY = "alert:last_trade_id"


def get_last_seen_trade_id():
    """Return the last alerted trade ID from Redis, or None if not set."""
    r = get_redis()
    val = r.get(_LAST_TRADE_KEY)
    return int(val) if val is not None else None


def set_last_seen_trade_id(trade_id: int) -> None:
    """Persist the last alerted trade ID to Redis."""
    r = get_redis()
    r.set(_LAST_TRADE_KEY, trade_id)


def get_new_trades(last_id) -> list:
    """Return trades with id > last_id (or all trades if last_id is None), ordered ascending."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if last_id is None:
                cur.execute(
                    "SELECT id, symbol, side, qty, price FROM trades ORDER BY id ASC"
                )
            else:
                cur.execute(
                    "SELECT id, symbol, side, qty, price FROM trades WHERE id > %s ORDER BY id ASC",
                    (last_id,),
                )
            rows = cur.fetchall()
    return rows


def check_new_trades(webhook_url: str, send_discord_fn) -> None:
    """Poll the trades table and send a Discord notification for each new trade."""
    last_id = get_last_seen_trade_id()
    trades = get_new_trades(last_id)

    for trade in trades:
        message = (
            f"🔔 **Trade placed** — {trade['side'].upper()} {trade['qty']} "
            f"{trade['symbol']} @ ${trade['price']:.2f}"
        )
        try:
            send_discord_fn(webhook_url, message)
        except Exception as exc:
            log.error("Failed to send Discord alert for trade %s: %s", trade["id"], exc)
        set_last_seen_trade_id(trade["id"])
