from datetime import date

from shared.db import get_conn

# get_conn() auto-commits on context exit (see shared/db.py). No explicit
# conn.commit() needed; rollback happens automatically on exception.


def get_today_pnl(today: date) -> float:
    """
    Returns today's realized P&L in dollars.
    Positive = profit, negative = loss.
    Returns 0.0 if no record exists yet for today.
    """
    sql = "SELECT realized_pnl FROM daily_pnl WHERE date = %s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (today,))
            row = cur.fetchone()
    return float(row[0]) if row else 0.0


def add_realized_pnl(amount: float, today: date) -> None:
    """
    Add `amount` to today's realized P&L (upsert).

    Called after each sell order fills to update the running daily total.
    Positive amount = profit, negative = loss.
    """
    sql = """
        INSERT INTO daily_pnl (date, realized_pnl)
        VALUES (%s, %s)
        ON CONFLICT (date)
        DO UPDATE SET
            realized_pnl = daily_pnl.realized_pnl + EXCLUDED.realized_pnl,
            updated_at = NOW()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (today, amount))


def is_circuit_breaker_triggered(today: date) -> bool:
    """
    Returns True if the circuit breaker has been triggered today.

    Persists across service restarts — the flag is stored in PostgreSQL.
    Returns False if no record exists for today (i.e. fresh day).
    """
    sql = "SELECT circuit_breaker_triggered FROM daily_pnl WHERE date = %s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (today,))
            row = cur.fetchone()
    return bool(row[0]) if row else False


def trigger_circuit_breaker(today: date) -> None:
    """
    Set the circuit_breaker_triggered flag for today (upsert).

    Once set, is_circuit_breaker_triggered() returns True for the rest of
    the day, blocking all new orders. Resets automatically the next calendar
    day (new date = new row).
    """
    sql = """
        INSERT INTO daily_pnl (date, circuit_breaker_triggered)
        VALUES (%s, TRUE)
        ON CONFLICT (date)
        DO UPDATE SET circuit_breaker_triggered = TRUE, updated_at = NOW()
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (today,))
