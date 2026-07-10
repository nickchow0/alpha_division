import json
import os
import re
from datetime import date as Date
from typing import Optional

import psycopg2.extras

from shared.db import get_conn
from shared.config import load_config
from shared.redis_client import get_redis


def get_open_positions() -> list:
    """
    Return all open positions — both long (last action = buy) and short
    (last action = short). Each row includes a 'direction' field ('long' or 'short').
    """
    sql = """
        SELECT symbol, side, qty, price, placed_at
        FROM (
            SELECT DISTINCT ON (symbol)
                symbol, side, qty, price, placed_at
            FROM trades
            WHERE status = 'filled'
            ORDER BY symbol, placed_at DESC
        ) latest
        WHERE side IN ('buy', 'short')
        ORDER BY placed_at DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = list(cur.fetchall())
    # Annotate each row with a human-readable direction
    for row in rows:
        row["direction"] = "long" if row["side"] == "buy" else "short"
    return rows


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
    """Return the most recent trades, newest first, including quoted_price for slippage display."""
    sql = """
        SELECT id, symbol, side, qty, price, quoted_price, status, placed_at, filled_at
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
    """
    Return the most recent health check result per API.

    Always includes alpaca, finnhub, fred. For AI providers, only returns
    the currently active one (claude or gemini) read from Redis — so the
    dashboard never shows a stale row from a previously selected provider.
    """
    r = get_redis()
    raw = r.get("config:ai_provider")
    active_ai = (raw.decode() if isinstance(raw, bytes) else raw) if raw else "claude"
    inactive_ai = "gemini" if active_ai == "claude" else "claude"

    sql = """
        SELECT DISTINCT ON (api_name)
            api_name, status, latency_ms, checked_at, error_message
        FROM api_health
        WHERE api_name != %s
        ORDER BY api_name, checked_at DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (inactive_ai,))
            return list(cur.fetchall())


def get_watchlist() -> list:
    """
    Return one row per configured watchlist symbol combining:
    - Latest market snapshot from Redis (price, RSI, SMAs) if available
    - Most recent AI decision from the decisions table if available

    Always returns all configured symbols, even those with no data yet.
    """
    symbols = load_config().get("watchlist", [])

    # Latest snapshot per symbol from Redis
    r = get_redis()
    snapshots: dict[str, dict] = {}
    for sym in symbols:
        raw = r.get(f"snapshot:{sym}")
        if raw:
            try:
                snapshots[sym] = json.loads(raw)
            except (json.JSONDecodeError, Exception):
                pass

    # Latest decision per symbol from DB
    decisions: dict[str, dict] = {}
    if symbols:
        placeholders = ",".join(["%s"] * len(symbols))
        sql = f"""
            SELECT DISTINCT ON (symbol)
                symbol, decision, confidence, decided_at, acted_on, skip_reason
            FROM decisions
            WHERE symbol IN ({placeholders})
            ORDER BY symbol, decided_at DESC
        """
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, symbols)
                for row in cur.fetchall():
                    decisions[row["symbol"]] = dict(row)

    # Merge into one row per symbol
    result = []
    for sym in symbols:
        snap = snapshots.get(sym, {})
        dec = decisions.get(sym, {})
        result.append({
            "symbol":      sym,
            "price":       snap.get("price"),
            "rsi":         snap.get("rsi"),
            "sma20":       snap.get("sma20"),
            "sma50":       snap.get("sma50"),
            "snapshot_at": snap.get("timestamp"),
            "decision":    dec.get("decision"),
            "confidence":  dec.get("confidence"),
            "acted_on":    dec.get("acted_on"),
            "skip_reason": dec.get("skip_reason"),
            "decided_at":  dec.get("decided_at"),
        })
    return result


def get_pnl_history(days: int = 30) -> list:
    """Return daily realized P&L for the last `days` days, oldest first."""
    sql = """
        SELECT date, realized_pnl
        FROM daily_pnl
        WHERE date >= CURRENT_DATE - (%s - 1) * INTERVAL '1 day'
        ORDER BY date ASC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (days,))
            return list(cur.fetchall())


def get_trade_activity(days: int = 30) -> list:
    """Return count of filled trades per day for the last `days` days, oldest first."""
    sql = """
        SELECT DATE(placed_at AT TIME ZONE 'America/New_York') AS date,
               COUNT(*) AS count
        FROM trades
        WHERE status = 'filled'
          AND placed_at >= NOW() - (%s * INTERVAL '1 day')
        GROUP BY DATE(placed_at AT TIME ZONE 'America/New_York')
        ORDER BY date ASC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (days,))
            return list(cur.fetchall())


def get_account_equity() -> Optional[float]:
    """
    Return the current account equity from Redis (written by the data service).

    Returns None if the data service hasn't published it yet, so callers can
    fall back gracefully (e.g. use paper_balance as the starting point).
    """
    r = get_redis()
    raw = r.get("account:equity")
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def get_unrealized_pnl() -> float:
    """
    Compute total unrealized P&L for all open positions (long and short).

    For long positions:  (current_price - entry_price) * qty
    For short positions: (entry_price - current_price) * qty
    Positions with no snapshot data yet are skipped (treated as 0).
    """
    positions = get_open_positions()
    if not positions:
        return 0.0
    r = get_redis()
    total = 0.0
    for pos in positions:
        raw = r.get(f"snapshot:{pos['symbol']}")
        if not raw:
            continue
        try:
            snap = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        current_price = snap.get("price")
        if current_price is not None:
            entry = float(pos["price"])
            qty = float(pos["qty"])
            if pos["direction"] == "short":
                total += (entry - float(current_price)) * qty
            else:
                total += (float(current_price) - entry) * qty
    return round(total, 2)


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


def get_trade_stats() -> dict:
    """
    Compute aggregate stats for all closed trades (both long and short pairs).

    Long pairs:  buy → sell, P&L = (sell_price - buy_price) * qty
    Short pairs: short → cover, P&L = (short_price - cover_price) * qty

    Each closing trade is matched via LATERAL join to the most recent filled
    opening trade for the same symbol before the close's filled_at.
    Safe because the bot holds at most one open position per symbol at a time.

    Returns a dict with keys:
        total_closed, wins, losses, win_rate_pct,
        avg_pnl, best_trade, worst_trade, avg_holding_hours
    All values are floats (win_rate_pct is 0.0 when total_closed == 0).
    """
    sql = """
        WITH pairs AS (
            -- Long pairs: sell closes a buy
            SELECT
                (s.price - b.price) * s.qty  AS pnl,
                EXTRACT(EPOCH FROM (s.filled_at - b.filled_at)) / 3600.0
                                              AS holding_hours
            FROM trades s
            JOIN LATERAL (
                SELECT price, filled_at
                FROM trades b
                WHERE b.symbol    = s.symbol
                  AND b.side      = 'buy'
                  AND b.status    = 'filled'
                  AND b.filled_at IS NOT NULL
                  AND b.filled_at < s.filled_at
                ORDER BY b.filled_at DESC
                LIMIT 1
            ) b ON true
            WHERE s.side        = 'sell'
              AND s.status      = 'filled'
              AND s.filled_at   IS NOT NULL

            UNION ALL

            -- Short pairs: cover closes a short
            SELECT
                (b.price - s.price) * s.qty  AS pnl,
                EXTRACT(EPOCH FROM (s.filled_at - b.filled_at)) / 3600.0
                                              AS holding_hours
            FROM trades s
            JOIN LATERAL (
                SELECT price, filled_at
                FROM trades b
                WHERE b.symbol    = s.symbol
                  AND b.side      = 'short'
                  AND b.status    = 'filled'
                  AND b.filled_at IS NOT NULL
                  AND b.filled_at < s.filled_at
                ORDER BY b.filled_at DESC
                LIMIT 1
            ) b ON true
            WHERE s.side        = 'cover'
              AND s.status      = 'filled'
              AND s.filled_at   IS NOT NULL
        )
        SELECT
            COUNT(*)                                                              AS total_closed,
            COUNT(*) FILTER (WHERE pnl > 0)                                      AS wins,
            COUNT(*) FILTER (WHERE pnl < 0)                                      AS losses,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE pnl > 0)
                / NULLIF(COUNT(*), 0),
                1
            )                                                                     AS win_rate_pct,
            ROUND(COALESCE(AVG(pnl),            0)::numeric, 2)                  AS avg_pnl,
            ROUND(COALESCE(MAX(pnl),            0)::numeric, 2)                  AS best_trade,
            ROUND(COALESCE(MIN(pnl),            0)::numeric, 2)                  AS worst_trade,
            ROUND(COALESCE(AVG(holding_hours),  0)::numeric, 1)                  AS avg_holding_hours
        FROM pairs
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone()

    def _f(key: str, default: float = 0.0) -> float:
        val = row.get(key)
        return float(val) if val is not None else default

    return {
        "total_closed":      int(row.get("total_closed") or 0),
        "wins":              int(row.get("wins")         or 0),
        "losses":            int(row.get("losses")       or 0),
        "win_rate_pct":      _f("win_rate_pct"),
        "avg_pnl":           _f("avg_pnl"),
        "best_trade":        _f("best_trade"),
        "worst_trade":       _f("worst_trade"),
        "avg_holding_hours": _f("avg_holding_hours"),
    }


def get_slippage_stats() -> dict:
    """
    Compute aggregate slippage stats across all trades that have quoted_price set.

    Slippage per trade:
        buy:  (quoted_price - price) * qty  — positive = paid more than close
        sell: (price - quoted_price) * qty  — positive = received less than close

    Returns a dict with keys:
        trades_with_quote: int — number of trades where quoted_price was captured
        avg_slippage:      float — mean slippage per trade in dollars
        total_slippage:    float — total slippage cost across all trades
        avg_slippage_pct:  float — mean slippage as % of trade value (price * qty)
    All monetary values are floats; 0.0 when no data.
    """
    sql = """
        SELECT
            COUNT(*)                                            AS trades_with_quote,
            ROUND(COALESCE(AVG(slippage), 0)::numeric, 2)      AS avg_slippage,
            ROUND(COALESCE(SUM(slippage), 0)::numeric, 2)      AS total_slippage,
            ROUND(COALESCE(AVG(slippage_pct), 0)::numeric, 4)  AS avg_slippage_pct
        FROM (
            SELECT
                CASE
                    WHEN side IN ('buy', 'cover') THEN (quoted_price - price) * qty
                    WHEN side IN ('sell', 'short') THEN (price - quoted_price) * qty
                END AS slippage,
                CASE
                    WHEN price > 0 AND qty > 0
                    THEN CASE
                        WHEN side IN ('buy', 'cover') THEN (quoted_price - price) / price * 100
                        WHEN side IN ('sell', 'short') THEN (price - quoted_price) / price * 100
                    END
                END AS slippage_pct
            FROM trades
            WHERE quoted_price IS NOT NULL
              AND price IS NOT NULL
        ) sub
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone()

    def _f(key: str) -> float:
        val = row.get(key)
        return float(val) if val is not None else 0.0

    return {
        "trades_with_quote": int(row.get("trades_with_quote") or 0),
        "avg_slippage":      _f("avg_slippage"),
        "total_slippage":    _f("total_slippage"),
        "avg_slippage_pct":  _f("avg_slippage_pct"),
    }


def get_analysis_stats(days: Optional[int] = None) -> dict:
    """
    Aggregate summary stats for AI decisions within the given time window.

    Parameters:
        days: number of days to look back (None = all time, omits date filter)

    Returns a dict with keys:
        total_decisions: int
        median_confidence: float (0.0 when no decisions)
        pct_above_threshold: float — % of decisions with confidence >= 0.65
        pct_acted_on: float — % of decisions (with confidence) where acted_on is True
        haiku_count: int
        sonnet_count: int
    """
    date_clause = "AND decided_at >= NOW() - (%s * INTERVAL '1 day')" if days is not None else ""
    # date_clause is constructed from a boolean only — never from user input — so f-string is safe.
    sql = f"""
        SELECT
            COUNT(*)                                                          AS total_decisions,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY confidence::numeric) AS median_confidence,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE confidence::numeric >= 0.65)
                / NULLIF(COUNT(*), 0),
                1
            )                                                                 AS pct_above_threshold,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE acted_on)
                / NULLIF(COUNT(*), 0),
                1
            )                                                                 AS pct_acted_on,
            COUNT(*) FILTER (WHERE model LIKE '%%haiku%%')                     AS haiku_count,
            COUNT(*) FILTER (WHERE model LIKE '%%sonnet%%')                   AS sonnet_count
        FROM decisions
        WHERE confidence IS NOT NULL
          {date_clause}
    """
    params = (days,) if days is not None else ()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()

    def _f(key: str) -> float:
        val = row.get(key)
        return float(val) if val is not None else 0.0

    return {
        "total_decisions":     int(row.get("total_decisions") or 0),
        "median_confidence":   _f("median_confidence"),
        "pct_above_threshold": _f("pct_above_threshold"),
        "pct_acted_on":        _f("pct_acted_on"),
        "haiku_count":         int(row.get("haiku_count") or 0),
        "sonnet_count":        int(row.get("sonnet_count") or 0),
    }


def get_confidence_histogram(days: Optional[int] = None) -> list:
    """
    Return decision counts in 20 confidence buckets of 5% each (0-5%, 5-10%, ..., 95-100%).

    Always returns exactly 20 rows including zero-count buckets (via generate_series left join).
    Buckets 1-13 cover 0-65% (below the 0.65 acting threshold); buckets 14-20 cover 65-100%.

    LEAST(confidence::numeric, 0.9999) guards against confidence=1.0 producing an out-of-range
    bucket 21 from WIDTH_BUCKET.

    Parameters:
        days: number of days to look back (None = all time)

    Each row: {"bucket": int (1-20), "label": str (e.g. "60-65%"), "count": int}
    """
    # date_clause is constructed from a boolean only — never from user input — so f-string is safe.
    date_clause = "AND decided_at >= NOW() - (%s * INTERVAL '1 day')" if days is not None else ""
    sql = f"""
        WITH buckets AS (
            SELECT
                WIDTH_BUCKET(LEAST(confidence::numeric, 0.9999), 0, 1, 20) AS bucket,
                COUNT(*) AS count
            FROM decisions
            WHERE confidence IS NOT NULL
              {date_clause}
            GROUP BY bucket
        )
        SELECT
            s.n                                                              AS bucket,
            (((s.n - 1) * 5)::text || '-' || (s.n * 5)::text || '%%')      AS label,
            COALESCE(b.count, 0)                                            AS count
        FROM generate_series(1, 20) s(n)
        LEFT JOIN buckets b ON b.bucket = s.n
        ORDER BY s.n
    """
    params = (days,) if days is not None else ()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def get_acted_on_rate_by_band(days: Optional[int] = None) -> list:
    """
    Return the acted-on rate per confidence band (20 buckets of 5%).

    Always returns exactly 20 rows including buckets with zero decisions.
    acted_pct is 0.0 when total == 0 (CASE handles this in SQL).

    Parameters:
        days: number of days to look back (None = all time)

    Each row: {"bucket": int, "label": str, "total": int, "acted": int, "acted_pct": float}
    """
    # date_clause is constructed from a boolean only — never from user input — so f-string is safe.
    date_clause = "AND decided_at >= NOW() - (%s * INTERVAL '1 day')" if days is not None else ""
    sql = f"""
        WITH buckets AS (
            SELECT
                WIDTH_BUCKET(LEAST(confidence::numeric, 0.9999), 0, 1, 20) AS bucket,
                COUNT(*)                           AS total,
                COUNT(*) FILTER (WHERE acted_on)   AS acted
            FROM decisions
            WHERE confidence IS NOT NULL
              {date_clause}
            GROUP BY bucket
        )
        SELECT
            s.n                                                              AS bucket,
            (((s.n - 1) * 5)::text || '-' || (s.n * 5)::text || '%%')      AS label,
            COALESCE(b.total, 0)                                            AS total,
            COALESCE(b.acted, 0)                                             AS acted,
            CASE
                WHEN COALESCE(b.total, 0) = 0 THEN 0.0
                ELSE ROUND(100.0 * b.acted / b.total, 1)
            END                                                              AS acted_pct
        FROM generate_series(1, 20) s(n)
        LEFT JOIN buckets b ON b.bucket = s.n
        ORDER BY s.n
    """
    params = (days,) if days is not None else ()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
    return [{**dict(r), "acted_pct": float(r["acted_pct"])} for r in rows]


def get_win_rate_by_band(days: Optional[int] = None) -> list:
    """
    Return win rate per confidence band for closed trades (matched buy+sell pairs).

    Uses the confidence stored on the opening trade (buy/short), which is always
    populated. The original join through decisions→signals→trades.signal_id was
    broken because signal_id is never set on trade rows.

    Parameters:
        days: number of days to look back based on sell/cover filled_at (None = all time)

    Returns only buckets where sample_size > 0. Returns [] when no closed trades exist.
    Each row: {"bucket": int, "label": str, "sample_size": int, "wins": int, "win_rate_pct": float}
    """
    # date_clause is constructed from a boolean only — never from user input — so f-string is safe.
    date_clause = "AND s.filled_at >= NOW() - (%s * INTERVAL '1 day')" if days is not None else ""
    sql = f"""
        WITH closed AS (
            -- Long trades: sell closes a buy; confidence from the buy order
            SELECT
                b.confidence,
                (s.price - b.price) * s.qty > 0 AS is_win
            FROM trades s
            JOIN LATERAL (
                SELECT price, confidence FROM trades b
                WHERE b.symbol    = s.symbol
                  AND b.side      = 'buy'
                  AND b.status    = 'filled'
                  AND b.filled_at IS NOT NULL
                  AND b.filled_at < s.filled_at
                ORDER BY b.filled_at DESC
                LIMIT 1
            ) b ON true
            WHERE s.side = 'sell'
              AND s.status = 'filled'
              AND s.filled_at IS NOT NULL
              AND b.confidence IS NOT NULL
              {date_clause}

            UNION ALL

            -- Short trades: cover closes a short; confidence from the short order
            SELECT
                b.confidence,
                (b.price - s.price) * s.qty > 0 AS is_win
            FROM trades s
            JOIN LATERAL (
                SELECT price, confidence FROM trades b
                WHERE b.symbol    = s.symbol
                  AND b.side      = 'short'
                  AND b.status    = 'filled'
                  AND b.filled_at IS NOT NULL
                  AND b.filled_at < s.filled_at
                ORDER BY b.filled_at DESC
                LIMIT 1
            ) b ON true
            WHERE s.side = 'cover'
              AND s.status = 'filled'
              AND s.filled_at IS NOT NULL
              AND b.confidence IS NOT NULL
              {date_clause}
        ),
        bucketed AS (
            SELECT
                WIDTH_BUCKET(LEAST(confidence::numeric, 0.9999), 0, 1, 20) AS bucket,
                COUNT(*)                          AS sample_size,
                COUNT(*) FILTER (WHERE is_win)    AS wins
            FROM closed
            GROUP BY bucket
        )
        SELECT
            bucket,
            (((bucket - 1) * 5)::text || '-' || (bucket * 5)::text || '%%') AS label,
            sample_size,
            wins,
            ROUND(100.0 * wins / NULLIF(sample_size, 0), 1)                 AS win_rate_pct
        FROM bucketed
        WHERE sample_size > 0
        ORDER BY bucket
    """
    params = (days, days) if days is not None else ()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
    return [{**dict(r), "win_rate_pct": float(r["win_rate_pct"] or 0)} for r in rows]


# ---------------------------------------------------------------------------
# AI provider settings
# ---------------------------------------------------------------------------

_AI_PROVIDER_KEY    = "config:ai_provider"
_CLAUDE_MODEL_KEY   = "config:claude_model"
_GEMINI_MODEL_KEY   = "config:gemini_model"

CLAUDE_MODELS = ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7"]
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro"]

_MODELS_CACHE_KEY = "cache:available_models"
_MODELS_CACHE_TTL = 24 * 60 * 60  # 24 hours

# Gemini model names that are not useful for text generation / codegen
_GEMINI_EXCLUDE = re.compile(
    r"tts|image|robotics|lyria|veo|embedding|nano-banana|antigravity"
    r"|computer-use|deep-research|gemma"
    r"|gemini-2\.0",  # 2.0 generation deprecated / routes to thinking variants
    re.IGNORECASE,
)
# Only include stable non-preview Gemini models (allow -001 pinned versions)
_GEMINI_STABLE = re.compile(r"^models/gemini-[\d.]+-(?:flash|pro)(?:-\d+)?$")


def get_available_models() -> dict:
    """Return {'claude': [...], 'gemini': [...], 'ollama': [...]} fetched from each API.

    Claude and Gemini results are cached in Redis for 24 hours. Falls back to
    the hardcoded CLAUDE_MODELS / GEMINI_MODELS lists if either API call fails.
    Ollama is always read fresh from config (no API fetch needed).
    """
    cfg = load_config()
    ollama_model = cfg.get("analysis", {}).get("ollama_model", "")
    ollama_entry = [ollama_model] if ollama_model else []

    r = get_redis()
    cached = r.get(_MODELS_CACHE_KEY)
    if cached:
        try:
            result = json.loads(cached)
            result["ollama"] = ollama_entry
            return result
        except Exception:
            pass

    claude_key = os.environ.get("ANTHROPIC_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")

    claude_models = _fetch_claude_models(claude_key)
    gemini_models = _fetch_gemini_models(gemini_key)

    result = {"claude": claude_models, "gemini": gemini_models}
    try:
        r.setex(_MODELS_CACHE_KEY, _MODELS_CACHE_TTL, json.dumps(result))
    except Exception:
        pass
    result["ollama"] = ollama_entry
    return result


_CLAUDE_DATED = re.compile(r"-20\d{6}$")       # e.g. claude-sonnet-4-5-20250929
_CLAUDE_VERSIONED = re.compile(r"^claude-\w+-\d+-\d+$")  # must have family-major-minor


def _fetch_claude_models(api_key: str) -> list[str]:
    if not api_key:
        return CLAUDE_MODELS
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        all_ids = [m.id for m in client.models.list() if m.id.startswith("claude-")]

        undated = {m for m in all_ids if not _CLAUDE_DATED.search(m)}

        # For dated models whose base name (date stripped) isn't already present,
        # synthesize it — e.g. claude-haiku-4-5-20251001 → claude-haiku-4-5.
        # The base name is a valid API alias even if list_models() omits it.
        for m in all_ids:
            if _CLAUDE_DATED.search(m):
                base = _CLAUDE_DATED.sub("", m)
                if base not in undated:
                    undated.add(base)

        # Drop names without a proper major.minor version (e.g. claude-sonnet-4)
        models = sorted(
            [m for m in undated if _CLAUDE_VERSIONED.match(m)],
            reverse=True,
        )
        return models if models else CLAUDE_MODELS
    except Exception:
        return CLAUDE_MODELS


def _fetch_gemini_models(api_key: str) -> list[str]:
    if not api_key:
        return GEMINI_MODELS
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        names = []
        for m in genai.list_models():
            if "generateContent" not in m.supported_generation_methods:
                continue
            name = m.name  # e.g. "models/gemini-2.5-flash"
            if _GEMINI_EXCLUDE.search(name):
                continue
            if not _GEMINI_STABLE.match(name):
                continue
            # Strip "models/" prefix
            names.append(name.replace("models/", ""))
        # Sort newest first by version number
        names.sort(key=lambda s: [int(x) for x in re.findall(r"\d+", s)], reverse=True)
        return names if names else GEMINI_MODELS
    except Exception:
        return GEMINI_MODELS

_ML_CODEGEN_PROVIDER_KEY     = "config:ml_codegen_provider"
_ML_CODEGEN_CLAUDE_MODEL_KEY = "config:ml_codegen_claude_model"
_ML_CODEGEN_GEMINI_MODEL_KEY = "config:ml_codegen_gemini_model"


def get_ai_settings() -> dict:
    """
    Return the current AI provider settings.

    Reads from Redis (set via the dashboard), falling back to config.toml
    defaults if no override has been saved yet.
    """
    cfg = load_config()
    analysis_cfg = cfg.get("analysis", {})
    r = get_redis()

    provider     = (r.get(_AI_PROVIDER_KEY)    or analysis_cfg.get("ai_provider",  "claude"))
    claude_model = (r.get(_CLAUDE_MODEL_KEY)   or analysis_cfg.get("claude_model", "claude-haiku-4-5"))
    gemini_model = (r.get(_GEMINI_MODEL_KEY)   or analysis_cfg.get("gemini_model", "gemini-2.5-flash"))

    # Redis returns bytes; decode if needed
    if isinstance(provider, bytes):
        provider = provider.decode()
    if isinstance(claude_model, bytes):
        claude_model = claude_model.decode()
    if isinstance(gemini_model, bytes):
        gemini_model = gemini_model.decode()

    ollama_model = analysis_cfg.get("ollama_model", "")

    available = get_available_models()
    return {
        "provider":     provider,
        "claude_model": claude_model,
        "gemini_model": gemini_model,
        "claude_models": available["claude"],
        "gemini_models": available["gemini"],
        "ollama_model": ollama_model,
        "ollama_models": available["ollama"],
    }


def set_ai_provider(provider: str, model: str) -> None:
    """
    Persist AI provider and model selection to Redis.

    The analysis service reads these keys on each cycle so changes take
    effect within seconds — no restart required.

    Raises ValueError for unknown provider or model values.
    """
    available = get_available_models()
    if provider not in ("claude", "gemini", "ollama"):
        raise ValueError(f"Unknown provider '{provider}' — must be 'claude', 'gemini', or 'ollama'")
    if provider == "claude" and model not in available["claude"]:
        raise ValueError(f"Unknown Claude model '{model}'")
    if provider == "gemini" and model not in available["gemini"]:
        raise ValueError(f"Unknown Gemini model '{model}'")
    # ollama: no model to store — model is config-only

    r = get_redis()
    r.set(_AI_PROVIDER_KEY, provider)
    if provider == "claude":
        r.set(_CLAUDE_MODEL_KEY, model)
    elif provider == "gemini":
        r.set(_GEMINI_MODEL_KEY, model)
    # ollama: nothing extra to write

    # Signal the data service to run an immediate AI health check
    r.set("health:ai_check_requested", "1")


def get_ml_codegen_settings() -> dict:
    """
    Return the current ML codegen provider settings.

    Reads from Redis, falling back to config.toml [ml] defaults.
    """
    cfg = load_config()
    ml_cfg = cfg.get("ml", {})
    r = get_redis()

    provider     = r.get(_ML_CODEGEN_PROVIDER_KEY)     or ml_cfg.get("codegen_provider", "claude")
    claude_model = r.get(_ML_CODEGEN_CLAUDE_MODEL_KEY) or ml_cfg.get("codegen_model", "claude-sonnet-4-6")
    gemini_model = r.get(_ML_CODEGEN_GEMINI_MODEL_KEY) or "gemini-2.5-flash"

    if isinstance(provider, bytes):
        provider = provider.decode()
    if isinstance(claude_model, bytes):
        claude_model = claude_model.decode()
    if isinstance(gemini_model, bytes):
        gemini_model = gemini_model.decode()

    ollama_codegen_model = ml_cfg.get("ollama_codegen_model", "")

    available = get_available_models()
    return {
        "codegen_provider":     provider,
        "codegen_claude_model": claude_model,
        "codegen_gemini_model": gemini_model,
        "claude_models":        available["claude"],
        "gemini_models":        available["gemini"],
        "codegen_ollama_model": ollama_codegen_model,
    }


def set_ml_codegen_provider(provider: str, model: str) -> None:
    """
    Persist ML codegen provider and model to Redis.

    Raises ValueError for unknown provider or model values.
    """
    available = get_available_models()
    if provider not in ("claude", "gemini", "ollama"):
        raise ValueError(f"Unknown provider '{provider}' — must be 'claude', 'gemini', or 'ollama'")
    if provider == "claude" and model not in available["claude"]:
        raise ValueError(f"Unknown Claude model '{model}'")
    if provider == "gemini" and model not in available["gemini"]:
        raise ValueError(f"Unknown Gemini model '{model}'")
    # ollama: no model to store — model is config-only

    r = get_redis()
    r.set(_ML_CODEGEN_PROVIDER_KEY, provider)
    if provider == "claude":
        r.set(_ML_CODEGEN_CLAUDE_MODEL_KEY, model)
    elif provider == "gemini":
        r.set(_ML_CODEGEN_GEMINI_MODEL_KEY, model)
    # ollama: nothing extra to write
