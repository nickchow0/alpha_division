"""
Integration tests: database schema and write/read contracts.

Verifies that:
- All required tables exist
- FK constraints are enforced
- UNIQUE constraint on daily_pnl.date is enforced
- The full decision->signal->trade chain can be written and read back
- daily_pnl upsert behaviour is correct
"""
import pytest
import psycopg2

# ---------- schema ----------

def test_all_tables_exist(db_cursor):
    db_cursor.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    )
    tables = {row[0] for row in db_cursor.fetchall()}
    assert {"api_health", "decisions", "signals", "trades", "daily_pnl"} <= tables


def test_decisions_columns(db_cursor):
    db_cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='decisions' AND table_schema='public'"
    )
    cols = {row[0] for row in db_cursor.fetchall()}
    assert {"id", "symbol", "decision", "confidence", "reasoning", "model",
            "acted_on", "skip_reason", "decided_at"} <= cols


def test_trades_columns(db_cursor):
    db_cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='trades' AND table_schema='public'"
    )
    cols = {row[0] for row in db_cursor.fetchall()}
    assert {"id", "symbol", "side", "qty", "price", "alpaca_order_id",
            "signal_id", "status", "placed_at", "filled_at"} <= cols


# ---------- constraints ----------

def test_signal_fk_enforced(raw_db):
    with raw_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cur.execute(
                "INSERT INTO signals (symbol, decision, confidence, decision_id) "
                "VALUES ('AAPL', 'buy', 0.8, 99999)"
            )
    raw_db.rollback()


def test_trade_fk_enforced(raw_db, db_cursor):
    db_cursor.execute(
        "INSERT INTO decisions (symbol, decision, confidence, reasoning, model, acted_on) "
        "VALUES ('AAPL', 'buy', 0.8, 'test', 'claude-haiku', TRUE) RETURNING id"
    )
    decision_id = db_cursor.fetchone()[0]
    db_cursor.execute(
        "INSERT INTO signals (symbol, decision, confidence, decision_id) "
        "VALUES ('AAPL', 'buy', 0.8, %s) RETURNING id", (decision_id,)
    )

    with raw_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            cur.execute(
                "INSERT INTO trades (symbol, side, qty, status, signal_id) "
                "VALUES ('AAPL', 'buy', 10, 'filled', 99999)"
            )
    raw_db.rollback()


def test_daily_pnl_date_unique(raw_db, db_cursor):
    db_cursor.execute(
        "INSERT INTO daily_pnl (date, realized_pnl) VALUES ('2026-01-01', 100.0)"
    )
    with raw_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO daily_pnl (date, realized_pnl) VALUES ('2026-01-01', 200.0)"
            )
    raw_db.rollback()


# ---------- full chain ----------

def test_full_decision_signal_trade_chain(db_cursor):
    db_cursor.execute(
        "INSERT INTO decisions (symbol, decision, confidence, reasoning, model, acted_on) "
        "VALUES ('AAPL', 'buy', 0.85, 'Strong RSI', 'claude-haiku', TRUE) RETURNING id"
    )
    decision_id = db_cursor.fetchone()[0]

    db_cursor.execute(
        "INSERT INTO signals (symbol, decision, confidence, decision_id) "
        "VALUES ('AAPL', 'buy', 0.85, %s) RETURNING id", (decision_id,)
    )
    signal_id = db_cursor.fetchone()[0]

    db_cursor.execute(
        "INSERT INTO trades (symbol, side, qty, price, signal_id, status) "
        "VALUES ('AAPL', 'buy', 10, 150.25, %s, 'filled')", (signal_id,)
    )

    # Join query matches what dashboard uses
    db_cursor.execute(
        "SELECT t.symbol, t.side, t.qty, d.reasoning "
        "FROM trades t "
        "JOIN signals s ON t.signal_id = s.id "
        "JOIN decisions d ON s.decision_id = d.id "
        "WHERE t.status = 'filled'"
    )
    row = db_cursor.fetchone()
    assert row == ("AAPL", "buy", 10, "Strong RSI")


def test_daily_pnl_on_conflict_update(db_cursor):
    db_cursor.execute(
        "INSERT INTO daily_pnl (date, realized_pnl) VALUES ('2026-01-01', 50.0)"
    )
    db_cursor.execute(
        "INSERT INTO daily_pnl (date, realized_pnl) "
        "VALUES ('2026-01-01', 75.0) "
        "ON CONFLICT (date) DO UPDATE SET realized_pnl = daily_pnl.realized_pnl + EXCLUDED.realized_pnl"
    )
    db_cursor.execute("SELECT realized_pnl FROM daily_pnl WHERE date = '2026-01-01'")
    assert float(db_cursor.fetchone()[0]) == 125.0
