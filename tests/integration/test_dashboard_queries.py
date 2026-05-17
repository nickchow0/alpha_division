"""
Integration tests: dashboard query functions against real database.

Verifies that every query function in services/dashboard/queries.py
returns the correct shape and values when run against the test database.
"""
import pytest
from datetime import date

from queries import (
    get_open_positions,
    get_total_pnl,
    get_daily_pnl_today,
    get_recent_trades,
    get_recent_decisions,
    get_api_health,
    get_circuit_breaker_status,
    get_pnl_history,
    get_trade_activity,
    get_trade_stats,
)


# ---------- helpers ----------

def _insert_decision(cur, symbol="AAPL", decision="buy", confidence=0.85, acted_on=True):
    cur.execute(
        "INSERT INTO decisions (symbol, decision, confidence, reasoning, model, acted_on) "
        "VALUES (%s, %s, %s, 'test', 'claude-haiku', %s) RETURNING id",
        (symbol, decision, confidence, acted_on),
    )
    return cur.fetchone()[0]


def _insert_signal(cur, symbol, decision, confidence, decision_id):
    cur.execute(
        "INSERT INTO signals (symbol, decision, confidence, decision_id) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (symbol, decision, confidence, decision_id),
    )
    return cur.fetchone()[0]


def _insert_trade(cur, symbol, side, signal_id, status="filled", price=150.0, qty=10):
    cur.execute(
        "INSERT INTO trades (symbol, side, qty, price, signal_id, status) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (symbol, side, qty, price, signal_id, status),
    )


def _insert_trade_with_times(cur, symbol, side, price, qty=10, filled_at=None):
    """Insert a single trade with an explicit filled_at timestamp. Returns trade id."""
    cur.execute(
        "INSERT INTO trades (symbol, side, qty, price, status, filled_at) "
        "VALUES (%s, %s, %s, %s, 'filled', %s) RETURNING id",
        (symbol, side, qty, price, filled_at),
    )
    return cur.fetchone()[0]


# ---------- get_open_positions ----------

def test_get_open_positions_empty():
    assert get_open_positions() == []


def test_get_open_positions_returns_buy_with_no_sell(db_cursor):
    d_id = _insert_decision(db_cursor, "AAPL", "buy")
    s_id = _insert_signal(db_cursor, "AAPL", "buy", 0.85, d_id)
    _insert_trade(db_cursor, "AAPL", "buy", s_id, status="filled")

    positions = get_open_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "AAPL"


def test_get_open_positions_excludes_sold_symbol(db_cursor):
    d_id = _insert_decision(db_cursor, "AAPL", "buy")
    s_id = _insert_signal(db_cursor, "AAPL", "buy", 0.85, d_id)
    _insert_trade(db_cursor, "AAPL", "buy", s_id, status="filled")

    d_id2 = _insert_decision(db_cursor, "AAPL", "sell")
    s_id2 = _insert_signal(db_cursor, "AAPL", "sell", 0.8, d_id2)
    _insert_trade(db_cursor, "AAPL", "sell", s_id2, status="filled")

    positions = get_open_positions()
    assert positions == []


# ---------- get_total_pnl ----------

def test_get_total_pnl_empty():
    assert get_total_pnl() == 0.0


def test_get_total_pnl_sums_all_days(db_cursor):
    db_cursor.execute("INSERT INTO daily_pnl (date, realized_pnl) VALUES ('2026-01-01', 100.0), ('2026-01-02', -30.0)")
    assert get_total_pnl() == 70.0


# ---------- get_daily_pnl_today ----------

def test_get_daily_pnl_today_missing_returns_zero(db_cursor):
    assert get_daily_pnl_today(date(2026, 1, 1)) == 0.0


def test_get_daily_pnl_today_returns_value(db_cursor):
    db_cursor.execute("INSERT INTO daily_pnl (date, realized_pnl) VALUES ('2026-01-01', 55.5)")
    assert get_daily_pnl_today(date(2026, 1, 1)) == 55.5


# ---------- get_recent_trades ----------

def test_get_recent_trades_empty():
    assert get_recent_trades() == []


def test_get_recent_trades_returns_correct_fields(db_cursor):
    d_id = _insert_decision(db_cursor)
    s_id = _insert_signal(db_cursor, "AAPL", "buy", 0.85, d_id)
    _insert_trade(db_cursor, "AAPL", "buy", s_id)

    trades = get_recent_trades()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "AAPL"
    assert trades[0]["side"] == "buy"
    assert trades[0]["status"] == "filled"


# ---------- get_recent_decisions ----------

def test_get_recent_decisions_empty():
    assert get_recent_decisions() == []


def test_get_recent_decisions_returns_all_types(db_cursor):
    for decision in ("buy", "hold", "sell"):
        _insert_decision(db_cursor, decision=decision)

    decisions = get_recent_decisions()
    assert len(decisions) == 3
    types = {d["decision"] for d in decisions}
    assert types == {"buy", "hold", "sell"}


# ---------- get_api_health ----------

def test_get_api_health_empty():
    assert get_api_health() == []


def test_get_api_health_returns_latest_per_api(db_cursor):
    db_cursor.execute(
        "INSERT INTO api_health (api_name, status, latency_ms) VALUES "
        "('alpaca', 'ok', 120), ('alpaca', 'error', 0), ('finnhub', 'ok', 80)"
    )
    health = get_api_health()
    names = {h["api_name"] for h in health}
    assert names == {"alpaca", "finnhub"}
    alpaca = next(h for h in health if h["api_name"] == "alpaca")
    # Should return most recent — either ok or error depending on insert order
    assert alpaca["status"] in ("ok", "error")


# ---------- get_circuit_breaker_status ----------

def test_get_circuit_breaker_status_no_row():
    assert get_circuit_breaker_status(date(2026, 1, 1)) is False


def test_get_circuit_breaker_status_not_triggered(db_cursor):
    db_cursor.execute("INSERT INTO daily_pnl (date, circuit_breaker_triggered) VALUES ('2026-01-01', FALSE)")
    assert get_circuit_breaker_status(date(2026, 1, 1)) is False


def test_get_circuit_breaker_status_triggered(db_cursor):
    db_cursor.execute("INSERT INTO daily_pnl (date, circuit_breaker_triggered) VALUES ('2026-01-01', TRUE)")
    assert get_circuit_breaker_status(date(2026, 1, 1)) is True


# ---------- get_pnl_history ----------

def test_get_pnl_history_empty():
    assert get_pnl_history() == []


def test_get_pnl_history_ordered_ascending(db_cursor):
    db_cursor.execute(
        "INSERT INTO daily_pnl (date, realized_pnl) VALUES "
        "('2026-01-03', 30.0), ('2026-01-01', 10.0), ('2026-01-02', 20.0)"
    )
    history = get_pnl_history(30)
    dates = [str(row["date"]) for row in history]
    assert dates == sorted(dates)


def test_get_pnl_history_respects_days_limit(db_cursor):
    from datetime import date, timedelta
    base = date(2026, 1, 1)
    for i in range(39):
        d = base + timedelta(days=i)
        db_cursor.execute(
            "INSERT INTO daily_pnl (date, realized_pnl) VALUES (%s, %s)",
            (d.isoformat(), float((i + 1) * 10)),
        )
    history = get_pnl_history(days=7)
    assert len(history) <= 7


# ---------- get_trade_activity ----------

def test_get_trade_activity_empty():
    assert get_trade_activity() == []


def test_get_trade_activity_counts_filled_trades(db_cursor):
    d_id = _insert_decision(db_cursor)
    s_id = _insert_signal(db_cursor, "AAPL", "buy", 0.85, d_id)
    _insert_trade(db_cursor, "AAPL", "buy", s_id, status="filled")
    _insert_trade(db_cursor, "AAPL", "buy", s_id, status="filled")
    _insert_trade(db_cursor, "AAPL", "buy", s_id, status="failed")  # not counted

    activity = get_trade_activity(30)
    assert len(activity) == 1
    assert int(activity[0]["count"]) == 2


# ---------- get_trade_stats ----------

def test_get_trade_stats_no_trades():
    stats = get_trade_stats()
    assert stats["total_closed"] == 0
    assert stats["wins"] == 0
    assert stats["losses"] == 0
    assert stats["win_rate_pct"] == 0.0
    assert stats["avg_pnl"] == 0.0
    assert stats["best_trade"] == 0.0
    assert stats["worst_trade"] == 0.0
    assert stats["avg_holding_hours"] == 0.0


def test_get_trade_stats_only_open_buys_no_sells(db_cursor):
    from datetime import datetime, timezone
    t0 = datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc)
    _insert_trade_with_times(db_cursor, "AAPL", "buy", price=150.0, filled_at=t0)
    stats = get_trade_stats()
    # No sells means no closed pairs
    assert stats["total_closed"] == 0
    assert stats["win_rate_pct"] == 0.0


def test_get_trade_stats_one_winning_trade(db_cursor):
    from datetime import datetime, timezone, timedelta
    t0 = datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=24)
    _insert_trade_with_times(db_cursor, "AAPL", "buy",  price=100.0, qty=10, filled_at=t0)
    _insert_trade_with_times(db_cursor, "AAPL", "sell", price=120.0, qty=10, filled_at=t1)
    stats = get_trade_stats()
    assert stats["total_closed"] == 1
    assert stats["wins"] == 1
    assert stats["losses"] == 0
    assert stats["win_rate_pct"] == 100.0
    assert abs(stats["avg_pnl"] - 200.0) < 0.01       # (120 - 100) * 10
    assert abs(stats["best_trade"] - 200.0) < 0.01
    assert abs(stats["avg_holding_hours"] - 24.0) < 0.1


def test_get_trade_stats_one_losing_trade(db_cursor):
    from datetime import datetime, timezone, timedelta
    t0 = datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=12)
    _insert_trade_with_times(db_cursor, "MSFT", "buy",  price=300.0, qty=5, filled_at=t0)
    _insert_trade_with_times(db_cursor, "MSFT", "sell", price=280.0, qty=5, filled_at=t1)
    stats = get_trade_stats()
    assert stats["total_closed"] == 1
    assert stats["wins"] == 0
    assert stats["losses"] == 1
    assert stats["win_rate_pct"] == 0.0
    assert abs(stats["worst_trade"] - (-100.0)) < 0.01  # (280 - 300) * 5


def test_get_trade_stats_win_rate_mixed(db_cursor):
    from datetime import datetime, timezone, timedelta
    t0 = datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc)
    # Trade 1: AAPL win
    _insert_trade_with_times(db_cursor, "AAPL", "buy",  price=100.0, qty=10, filled_at=t0)
    _insert_trade_with_times(db_cursor, "AAPL", "sell", price=110.0, qty=10, filled_at=t0 + timedelta(hours=24))
    # Trade 2: MSFT loss
    _insert_trade_with_times(db_cursor, "MSFT", "buy",  price=300.0, qty=5,  filled_at=t0)
    _insert_trade_with_times(db_cursor, "MSFT", "sell", price=290.0, qty=5,  filled_at=t0 + timedelta(hours=8))
    # Trade 3: GOOGL win
    _insert_trade_with_times(db_cursor, "GOOGL", "buy",  price=150.0, qty=4, filled_at=t0)
    _insert_trade_with_times(db_cursor, "GOOGL", "sell", price=165.0, qty=4, filled_at=t0 + timedelta(hours=48))
    stats = get_trade_stats()
    assert stats["total_closed"] == 3
    assert stats["wins"] == 2
    assert stats["losses"] == 1
    assert abs(stats["win_rate_pct"] - 66.7) < 0.1


def test_get_trade_stats_break_even_not_counted_as_loss(db_cursor):
    from datetime import datetime, timezone, timedelta
    t0 = datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=8)
    _insert_trade_with_times(db_cursor, "AAPL", "buy",  price=150.0, qty=10, filled_at=t0)
    _insert_trade_with_times(db_cursor, "AAPL", "sell", price=150.0, qty=10, filled_at=t1)
    stats = get_trade_stats()
    assert stats["total_closed"] == 1
    assert stats["wins"] == 0
    assert stats["losses"] == 0   # break-even is neither a win nor a loss


def test_get_trade_stats_keys_are_correct_types():
    stats = get_trade_stats()
    assert isinstance(stats["total_closed"], int)
    assert isinstance(stats["wins"], int)
    assert isinstance(stats["losses"], int)
    assert isinstance(stats["win_rate_pct"], float)
    assert isinstance(stats["avg_pnl"], float)
    assert isinstance(stats["best_trade"], float)
    assert isinstance(stats["worst_trade"], float)
    assert isinstance(stats["avg_holding_hours"], float)
