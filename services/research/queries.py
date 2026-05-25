# services/research/queries.py
from datetime import date as Date
from typing import Optional

import psycopg2.extras
from psycopg2.extras import execute_values, RealDictCursor

from shared.db import get_conn


def save_strategy(
    name: str,
    description: str,
    hypothesis: str,
    code: str,
    code_hash: str,
    triggered_by: str,
) -> int:
    """Insert a new strategy in draft status. Returns the new strategy id."""
    sql = """
        INSERT INTO strategies (name, description, hypothesis, code, code_hash, status, triggered_by)
        VALUES (%s, %s, %s, %s, %s, 'draft', %s)
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (name, description, hypothesis, code, code_hash, triggered_by))
            return cur.fetchone()["id"]


def get_strategies() -> list:
    """Return all strategies with their latest backtest metrics (LATERAL join)."""
    sql = """
        SELECT
            s.id, s.name, s.description, s.hypothesis, s.code, s.code_hash,
            s.status, s.triggered_by, s.created_at,
            br.symbol, br.data_source, br.ran_at,
            br.sharpe_ratio, br.win_rate_pct, br.max_drawdown_pct,
            br.total_return_pct, br.trade_count
        FROM strategies s
        LEFT JOIN LATERAL (
            SELECT symbol, data_source, ran_at,
                   sharpe_ratio, win_rate_pct, max_drawdown_pct,
                   total_return_pct, trade_count
            FROM backtest_runs
            WHERE strategy_id = s.id
            ORDER BY ran_at DESC NULLS LAST
            LIMIT 1
        ) br ON true
        ORDER BY s.created_at DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return list(cur.fetchall())


def save_backtest_run(
    strategy_id: int,
    symbol: str,
    start_date: Date,
    end_date: Date,
    data_source: str,
    params: dict,
    metrics: dict,
) -> int:
    """Insert a backtest run with metrics. Returns the new run id."""
    sql = """
        INSERT INTO backtest_runs (
            strategy_id, symbol, start_date, end_date, data_source,
            initial_capital, max_position_pct, stop_loss_pct, max_hold_bars,
            total_return_pct, sharpe_ratio, max_drawdown_pct,
            win_rate_pct, trade_count, avg_hold_bars
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s
        )
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (
                strategy_id, symbol, start_date, end_date, data_source,
                params["initial_capital"], params["max_position_pct"],
                params["stop_loss_pct"], params["max_hold_bars"],
                metrics.get("total_return_pct"), metrics.get("sharpe_ratio"),
                metrics.get("max_drawdown_pct"), metrics.get("win_rate_pct"),
                metrics.get("trade_count"), metrics.get("avg_hold_bars"),
            ))
            return cur.fetchone()["id"]


def save_backtest_trades(run_id: int, symbol: str, trades: list) -> None:
    """Bulk-insert backtest trades. No-op if trades is empty."""
    if not trades:
        return
    sql = """
        INSERT INTO backtest_trades
            (run_id, symbol, side, entry_bar, exit_bar,
             entry_price, exit_price, position_size, pnl, exit_reason)
        VALUES %s
    """
    rows = [
        (
            run_id, symbol, t.get("side", "buy"),
            t["entry_bar"], t.get("exit_bar"),
            t.get("entry_price"), t.get("exit_price"),
            t.get("position_size"), t.get("pnl"),
            t.get("exit_reason"),
        )
        for t in trades
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows)


def get_strategy_runs(strategy_id: int) -> list:
    """Return all backtest runs for a strategy, newest first."""
    sql = """
        SELECT id, strategy_id, symbol, start_date, end_date, data_source,
               initial_capital, max_position_pct, stop_loss_pct, max_hold_bars,
               total_return_pct, sharpe_ratio, max_drawdown_pct,
               win_rate_pct, trade_count, avg_hold_bars, critique, ran_at
        FROM backtest_runs
        WHERE strategy_id = %s
        ORDER BY ran_at DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (strategy_id,))
            return list(cur.fetchall())


def update_strategy_status(strategy_id: int, status: str) -> None:
    """Update a strategy's status."""
    sql = "UPDATE strategies SET status = %s WHERE id = %s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (status, strategy_id))


def get_candidates() -> list:
    """
    Return all candidate strategies with their best backtest run,
    sorted by Sharpe ratio descending.
    """
    sql = """
        SELECT
            s.id, s.name, s.description, s.hypothesis, s.code, s.code_hash,
            s.status, s.triggered_by, s.created_at,
            br.id         AS alp_run_id,
            br.symbol, br.start_date, br.end_date,
            br.total_return_pct, br.sharpe_ratio, br.max_drawdown_pct,
            br.win_rate_pct, br.trade_count, br.avg_hold_bars, br.critique
        FROM strategies s
        LEFT JOIN LATERAL (
            SELECT id, symbol, start_date, end_date,
                   total_return_pct, sharpe_ratio, max_drawdown_pct,
                   win_rate_pct, trade_count, avg_hold_bars, critique
            FROM backtest_runs
            WHERE strategy_id = s.id
            ORDER BY sharpe_ratio DESC NULLS LAST
            LIMIT 1
        ) br ON true
        WHERE s.status = 'candidate'
        ORDER BY br.sharpe_ratio DESC NULLS LAST
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
            return list(cur.fetchall())


def get_ml_runs(limit: int = 20) -> list:
    """Return recent ML pipeline runs, newest first."""
    sql = """
        SELECT id, ran_at, symbols_processed, patterns_found,
               strategies_generated, candidates_promoted, duration_seconds, error
        FROM ml_runs
        ORDER BY ran_at DESC
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (limit,))
            return list(cur.fetchall())


def get_run_trades(run_id: int) -> list:
    """Return all trades for a backtest run, ordered by entry_bar."""
    sql = """
        SELECT id, run_id, symbol, side, entry_bar, exit_bar,
               entry_price, exit_price, position_size, pnl, exit_reason
        FROM backtest_trades
        WHERE run_id = %s
        ORDER BY entry_bar
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (run_id,))
            return list(cur.fetchall())
