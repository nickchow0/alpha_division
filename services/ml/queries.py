"""services/ml/queries.py — DB helpers for the ML pipeline.

All functions use the shared get_conn context manager. The ML service
reads from and writes to:
  - ml_bars     (bar cache)
  - ml_runs     (pipeline run history)
  - strategies  (shared with Research service — inserts only, never modifies Research rows)
"""
import hashlib
import logging
from datetime import date
from typing import Optional

import psycopg2.extras
from psycopg2.extras import execute_values, RealDictCursor

from shared.db import get_conn

log = logging.getLogger("ml.queries")


# ── Bar cache ─────────────────────────────────────────────────────────────────

def get_cached_bars(symbol: str, start_date: date) -> list[dict]:
    """Return all cached bars for symbol on or after start_date, ascending."""
    sql = """
        SELECT symbol, bar_date AS date, open, high, low, close, volume
        FROM ml_bars
        WHERE symbol = %s AND bar_date >= %s
        ORDER BY bar_date ASC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (symbol, start_date))
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def save_bars(symbol: str, bars: list[dict]) -> None:
    """Upsert OHLCV bars into ml_bars. Ignores conflicts (same symbol+date)."""
    if not bars:
        return
    sql = """
        INSERT INTO ml_bars (symbol, bar_date, open, high, low, close, volume)
        VALUES %s
        ON CONFLICT (symbol, bar_date) DO NOTHING
    """
    rows = [
        (symbol, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"])
        for b in bars
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows)
    log.debug("Saved %d bars for %s", len(bars), symbol)


# ── Strategy persistence ──────────────────────────────────────────────────────

def save_ml_strategy(
    name: str,
    description: str,
    hypothesis: str,
    code: str,
    code_hash: str,
) -> int:
    """Insert an ML-discovered strategy with status='testing'. Returns strategy id."""
    sql = """
        INSERT INTO strategies
            (name, description, hypothesis, code, code_hash, status, triggered_by)
        VALUES (%s, %s, %s, %s, %s, 'testing', 'ml_discovery')
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (name, description, hypothesis, code, code_hash))
            return cur.fetchone()["id"]


# ── Run history ───────────────────────────────────────────────────────────────

def save_ml_run(
    symbols_processed: int,
    patterns_found: int,
    strategies_generated: int,
    candidates_promoted: int,
    duration_seconds: float,
    error: Optional[str] = None,
) -> int:
    """Write one row to ml_runs for this pipeline execution. Returns run id."""
    sql = """
        INSERT INTO ml_runs
            (symbols_processed, patterns_found, strategies_generated,
             candidates_promoted, duration_seconds, error)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (
                symbols_processed, patterns_found, strategies_generated,
                candidates_promoted, duration_seconds, error,
            ))
            return cur.fetchone()["id"]


# ── Migration bootstrap ───────────────────────────────────────────────────────

def ensure_ml_tables() -> None:
    """Create ml_bars and ml_runs if they don't exist. Safe to call on every startup."""
    sql = """
        CREATE TABLE IF NOT EXISTS ml_bars (
            id          SERIAL PRIMARY KEY,
            symbol      TEXT NOT NULL,
            bar_date    DATE NOT NULL,
            open        FLOAT,
            high        FLOAT,
            low         FLOAT,
            close       FLOAT,
            volume      BIGINT,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(symbol, bar_date)
        );
        CREATE TABLE IF NOT EXISTS ml_runs (
            id                   SERIAL PRIMARY KEY,
            ran_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbols_processed    INTEGER,
            patterns_found       INTEGER,
            strategies_generated INTEGER,
            candidates_promoted  INTEGER,
            duration_seconds     FLOAT,
            error                TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ml_bars_symbol_date ON ml_bars(symbol, bar_date);
        CREATE INDEX IF NOT EXISTS idx_ml_runs_ran_at ON ml_runs(ran_at DESC);
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    log.info("ML tables verified")
