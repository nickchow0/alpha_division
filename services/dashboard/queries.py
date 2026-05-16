from datetime import date as Date

import psycopg2.extras

from shared.db import get_conn


def get_open_positions() -> list:
    """
    Return open positions: the most recent filled trade per symbol where the
    last action was a buy (i.e. no subsequent filled sell).
    """
    sql = """
        SELECT symbol, qty, price, placed_at
        FROM (
            SELECT DISTINCT ON (symbol)
                symbol, side, qty, price, placed_at
            FROM trades
            WHERE status = 'filled'
            ORDER BY symbol, placed_at DESC
        ) latest
        WHERE side = 'buy'
        ORDER BY placed_at DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return list(cur.fetchall())


def get_total_pnl() -> float:
    """Return cumulative realized P&L across all days."""
    sql = "SELECT SUM(realized_pnl) AS total FROM daily_pnl"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone()
    if row is None or row["total"] is None:
        return 0.0
    return float(row["total"])


def get_daily_pnl_today(today: Date) -> float:
    """Return today's realized P&L, or 0.0 if no row exists yet."""
    sql = "SELECT realized_pnl FROM daily_pnl WHERE date = %s"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (today,))
            row = cur.fetchone()
    if row is None:
        return 0.0
    return float(row["realized_pnl"])


def get_recent_trades(limit: int = 100) -> list:
    """Return the most recent trades, newest first."""
    sql = """
        SELECT id, symbol, side, qty, price, status, placed_at, filled_at
        FROM trades
        ORDER BY placed_at DESC
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (limit,))
            return list(cur.fetchall())


def get_recent_decisions(limit: int = 100) -> list:
    """Return the most recent AI decisions, newest first."""
    sql = """
        SELECT id, symbol, decision, confidence, reasoning, model,
               acted_on, skip_reason, decided_at
        FROM decisions
        ORDER BY decided_at DESC
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (limit,))
            return list(cur.fetchall())


def get_api_health() -> list:
    """Return the most recent health check result per API."""
    sql = """
        SELECT DISTINCT ON (api_name)
            api_name, status, latency_ms, checked_at, error_message
        FROM api_health
        ORDER BY api_name, checked_at DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return list(cur.fetchall())


def get_watchlist() -> list:
    """Return the most recent AI decision per symbol (the current watchlist state)."""
    sql = """
        SELECT DISTINCT ON (symbol)
            symbol, decision, confidence, decided_at, acted_on
        FROM decisions
        ORDER BY symbol, decided_at DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return list(cur.fetchall())


def get_circuit_breaker_status(today: Date) -> bool:
    """Return True if the circuit breaker was triggered today."""
    sql = "SELECT circuit_breaker_triggered FROM daily_pnl WHERE date = %s"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (today,))
            row = cur.fetchone()
    if row is None:
        return False
    return bool(row["circuit_breaker_triggered"])
