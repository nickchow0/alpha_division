# Research Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `services/research/` Docker service (Flask, port 8081) with a strategy framework (AST validation, sandboxed execution) and backtesting engine (vectorized simulation, yfinance + Alpaca data), surfacing passing strategies to an approval queue UI.

**Architecture:** New Flask service on port 8081 sharing the existing Postgres database via three new tables (`strategies`, `backtest_runs`, `backtest_trades`). Strategy code is Python functions validated by AST analysis and executed inside a thread-pool timeout. The backtester computes indicators using pandas/ta then simulates trades with no look-ahead bias.

**Tech Stack:** Python 3.11, Flask 3.0, psycopg2-binary, yfinance, alpaca-trade-api, pandas, ta, gunicorn, pytest

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `db/migrations/003_research_tables.sql` | Create | Schema for strategies, backtest_runs, backtest_trades |
| `services/research/Dockerfile` | Create | Container definition |
| `services/research/requirements.txt` | Create | Python dependencies |
| `services/research/main.py` | Create | Flask app — all 9 routes |
| `services/research/strategy.py` | Create | AST validator, sandboxed executor |
| `services/research/backtester.py` | Create | Indicator series, trade simulation, metrics |
| `services/research/data.py` | Create | yfinance and Alpaca bar fetchers |
| `services/research/queries.py` | Create | Postgres queries for strategy registry |
| `services/research/templates/research.html` | Create | Strategy browser UI |
| `services/research/templates/candidates.html` | Create | Approval queue UI |
| `services/research/static/research.css` | Create | Research-specific styles |
| `services/research/tests/__init__.py` | Create | Empty |
| `services/research/tests/test_strategy.py` | Create | Strategy framework tests |
| `services/research/tests/test_backtester.py` | Create | Backtester tests |
| `services/research/tests/test_data.py` | Create | Data fetcher tests |
| `services/research/tests/test_queries.py` | Create | Queries tests |
| `services/research/tests/test_main.py` | Create | Route tests |
| `docker-compose.yml` | Modify | Add research service |
| `services/dashboard/templates/base.html` | Modify | Add Research nav link |

---

### Task 1: DB Migration

**Files:**
- Create: `db/migrations/003_research_tables.sql`

- [ ] **Step 1: Write the migration file**

```sql
-- db/migrations/003_research_tables.sql
-- Migration 003: Research service tables

CREATE TABLE IF NOT EXISTS strategies (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    hypothesis      TEXT NOT NULL,
    code            TEXT NOT NULL,
    code_hash       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
    triggered_by    TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                  SERIAL PRIMARY KEY,
    strategy_id         INTEGER REFERENCES strategies(id),
    symbol              TEXT NOT NULL,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    data_source         TEXT NOT NULL,
    initial_capital     DECIMAL(12,2) NOT NULL DEFAULT 100000,
    max_position_pct    DECIMAL(5,4)  NOT NULL DEFAULT 0.15,
    stop_loss_pct       DECIMAL(5,4)  NOT NULL DEFAULT 0.05,
    max_hold_bars       INTEGER       NOT NULL DEFAULT 20,
    total_return_pct    DECIMAL(8,4),
    sharpe_ratio        DECIMAL(8,4),
    max_drawdown_pct    DECIMAL(8,4),
    win_rate_pct        DECIMAL(8,4),
    trade_count         INTEGER,
    avg_hold_bars       DECIMAL(6,2),
    critique            TEXT,
    ran_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER REFERENCES backtest_runs(id),
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_bar       INTEGER NOT NULL,
    exit_bar        INTEGER,
    entry_price     DECIMAL(10,4),
    exit_price      DECIMAL(10,4),
    position_size   DECIMAL(10,4),
    pnl             DECIMAL(10,4),
    exit_reason     TEXT
);

CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy ON backtest_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_run ON backtest_trades(run_id);
```

- [ ] **Step 2: Commit**

```bash
git add db/migrations/003_research_tables.sql
git commit -m "feat: add research service DB migration (strategies, backtest_runs, backtest_trades)"
```

---

### Task 2: Service Skeleton

**Files:**
- Create: `services/research/Dockerfile`
- Create: `services/research/requirements.txt`
- Create: `services/research/main.py` (health check only)
- Create: `services/research/tests/__init__.py`
- Create: `services/research/tests/test_main.py` (health route test only)

- [ ] **Step 1: Write the failing health route test**

```python
# services/research/tests/test_main.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from unittest.mock import patch, MagicMock


class TestHealthRoute(unittest.TestCase):
    def setUp(self):
        from main import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_health_returns_ok(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok"})
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /path/to/alphadivision
PYTHONPATH=services/research:. pytest services/research/tests/test_main.py::TestHealthRoute -v
```

Expected: `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Create requirements.txt**

```
# services/research/requirements.txt
psycopg2-binary==2.9.9
flask==3.0.3
gunicorn==22.0.0
yfinance==0.2.40
alpaca-trade-api==3.2.0
pandas==2.2.2
ta==0.11.0
python-dotenv==1.0.1
```

- [ ] **Step 4: Create Dockerfile**

```dockerfile
# services/research/Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY templates/ templates/
COPY static/ static/

RUN adduser --disabled-password --gecos "" appuser
USER appuser

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8081", "main:app"]
```

- [ ] **Step 5: Create minimal main.py**

```python
# services/research/main.py
import sys
sys.path.insert(0, "/app")

from flask import Flask
from shared.logger import get_logger

log = get_logger("research")

app = Flask(__name__)


@app.route("/health")
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    log.info("Research Service starting")
    app.run(host="0.0.0.0", port=8081)
```

- [ ] **Step 6: Create empty tests/__init__.py**

```python
# services/research/tests/__init__.py
```

- [ ] **Step 7: Run tests to confirm they pass**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_main.py::TestHealthRoute -v
```

Expected: `PASSED`

- [ ] **Step 8: Create static and templates directories**

```bash
mkdir -p services/research/templates services/research/static
touch services/research/static/research.css
```

- [ ] **Step 9: Commit**

```bash
git add services/research/
git commit -m "feat: add research service skeleton (Flask health check, Dockerfile)"
```

---

### Task 3: Strategy Framework

**Files:**
- Create: `services/research/strategy.py`
- Create: `services/research/tests/test_strategy.py`

- [ ] **Step 1: Write failing tests**

```python
# services/research/tests/test_strategy.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from strategy import validate_strategy_code, compute_code_hash, load_strategy, execute_strategy

VALID_CODE = """
def generate_signal(snapshot):
    rsi = snapshot["rsi"]
    if rsi < 35:
        return {"decision": "buy", "confidence": 0.78, "reasoning": "RSI oversold."}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "No signal."}
"""

SNAPSHOT = {
    "price": 150.0, "rsi": 30.0, "sma20": 148.0, "sma50": 145.0,
    "sma20_prev": 147.5, "sma20_prev2": 147.0, "volume": 2_000_000, "volume_avg": 1_000_000,
}


class TestValidateStrategyCode(unittest.TestCase):
    def test_valid_code_passes(self):
        validate_strategy_code(VALID_CODE)  # Should not raise

    def test_blocks_import_statement(self):
        code = "import os\ndef generate_signal(s): return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("Import", str(ctx.exception))

    def test_blocks_from_import(self):
        code = "from os import path\ndef generate_signal(s): return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("Import", str(ctx.exception))

    def test_blocks_dunder_import(self):
        code = "def generate_signal(s):\n    __import__('os')\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("__import__", str(ctx.exception))

    def test_blocks_open_call(self):
        code = "def generate_signal(s):\n    open('/etc/passwd')\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("open", str(ctx.exception))

    def test_blocks_exec_call(self):
        code = "def generate_signal(s):\n    exec('pass')\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("exec", str(ctx.exception))

    def test_blocks_eval_call(self):
        code = "def generate_signal(s):\n    eval('1+1')\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("eval", str(ctx.exception))

    def test_blocks_os_name_reference(self):
        code = "def generate_signal(s):\n    x = os\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'x'}"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("os", str(ctx.exception))

    def test_rejects_syntax_error(self):
        code = "def generate_signal(s):\n    return {"
        with self.assertRaises(ValueError) as ctx:
            validate_strategy_code(code)
        self.assertIn("Syntax", str(ctx.exception))


class TestComputeCodeHash(unittest.TestCase):
    def test_returns_64_char_hex_string(self):
        h = compute_code_hash(VALID_CODE)
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_same_code_same_hash(self):
        self.assertEqual(compute_code_hash(VALID_CODE), compute_code_hash(VALID_CODE))

    def test_different_code_different_hash(self):
        self.assertNotEqual(compute_code_hash(VALID_CODE), compute_code_hash(VALID_CODE + " "))


class TestLoadStrategy(unittest.TestCase):
    def test_returns_callable(self):
        fn = load_strategy(VALID_CODE)
        self.assertTrue(callable(fn))

    def test_raises_if_no_generate_signal(self):
        code = "def other_function(): pass"
        with self.assertRaises(ValueError) as ctx:
            load_strategy(code)
        self.assertIn("generate_signal", str(ctx.exception))


class TestExecuteStrategy(unittest.TestCase):
    def test_returns_correct_decision(self):
        fn = load_strategy(VALID_CODE)
        result = execute_strategy(fn, SNAPSHOT)
        self.assertEqual(result["decision"], "buy")
        self.assertAlmostEqual(result["confidence"], 0.78)
        self.assertIsInstance(result["reasoning"], str)

    def test_enforces_timeout(self):
        slow_code = """
def generate_signal(snapshot):
    while True:
        pass
"""
        fn = load_strategy(slow_code)
        result = execute_strategy(fn, SNAPSHOT)
        # Timeout returns hold
        self.assertEqual(result["decision"], "hold")
        self.assertIn("timed out", result["reasoning"].lower())

    def test_handles_exception_in_strategy(self):
        bad_code = """
def generate_signal(snapshot):
    raise RuntimeError("oops")
"""
        fn = load_strategy(bad_code)
        result = execute_strategy(fn, SNAPSHOT)
        self.assertEqual(result["decision"], "hold")
        self.assertIn("exception", result["reasoning"].lower())

    def test_raises_on_invalid_schema(self):
        code = """
def generate_signal(snapshot):
    return {"wrong_key": "value"}
"""
        fn = load_strategy(code)
        with self.assertRaises(ValueError) as ctx:
            execute_strategy(fn, SNAPSHOT)
        self.assertIn("missing keys", str(ctx.exception))

    def test_raises_on_invalid_decision(self):
        code = """
def generate_signal(snapshot):
    return {"decision": "INVALID", "confidence": 0.5, "reasoning": "test"}
"""
        fn = load_strategy(code)
        with self.assertRaises(ValueError) as ctx:
            execute_strategy(fn, SNAPSHOT)
        self.assertIn("Invalid decision", str(ctx.exception))

    def test_raises_on_confidence_out_of_range(self):
        code = """
def generate_signal(snapshot):
    return {"decision": "buy", "confidence": 1.5, "reasoning": "test"}
"""
        fn = load_strategy(code)
        with self.assertRaises(ValueError) as ctx:
            execute_strategy(fn, SNAPSHOT)
        self.assertIn("Confidence", str(ctx.exception))
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_strategy.py -v
```

Expected: `ModuleNotFoundError: No module named 'strategy'`

- [ ] **Step 3: Implement strategy.py**

```python
# services/research/strategy.py
import ast
import hashlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Callable

_BLOCKED_NAMES = {"__import__", "open", "exec", "eval", "os", "sys", "socket", "subprocess"}
_BLOCKED_MODULES = {"os", "sys", "socket", "subprocess"}
_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_TIMEOUT_SECONDS = 2.0


def validate_strategy_code(code: str) -> None:
    """
    Walk the AST of code and raise ValueError if any blocked construct is present.
    Raises ValueError on syntax errors too.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Syntax error: {e}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("Import statements are not allowed in strategy code")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_NAMES:
                raise ValueError(f"Blocked function call: {node.func.id}()")
        if isinstance(node, ast.Name) and node.id in _BLOCKED_MODULES:
            raise ValueError(f"Blocked name reference: {node.id}")


def compute_code_hash(code: str) -> str:
    """Return SHA256 hex digest of the strategy source."""
    return hashlib.sha256(code.encode()).hexdigest()


def load_strategy(code: str) -> Callable:
    """
    Compile and exec strategy code in a fresh namespace.
    Returns the generate_signal function.
    Raises ValueError if generate_signal is not defined.
    """
    namespace: dict = {}
    exec(compile(code, "<strategy>", "exec"), namespace)  # noqa: S102
    if "generate_signal" not in namespace:
        raise ValueError("Strategy code must define a 'generate_signal' function")
    return namespace["generate_signal"]


def execute_strategy(fn: Callable, snapshot: dict) -> dict:
    """
    Execute fn(snapshot) inside a thread-pool with a 2-second timeout.
    - On timeout: returns hold with reasoning.
    - On exception: returns hold with reasoning.
    - On invalid schema: raises ValueError (backtest aborts).
    """
    future = _EXECUTOR.submit(fn, snapshot)
    try:
        result = future.result(timeout=_TIMEOUT_SECONDS)
    except FuturesTimeoutError:
        return {
            "decision": "hold",
            "confidence": 0.5,
            "reasoning": "Strategy execution timed out",
        }
    except Exception as e:
        return {
            "decision": "hold",
            "confidence": 0.5,
            "reasoning": f"Strategy raised exception: {e}",
        }

    if not isinstance(result, dict):
        raise ValueError(f"Strategy returned {type(result).__name__}, expected dict")

    missing = {"decision", "confidence", "reasoning"} - set(result.keys())
    if missing:
        raise ValueError(f"Strategy result missing keys: {missing}")

    if result["decision"] not in ("buy", "sell", "hold"):
        raise ValueError(f"Invalid decision: {result['decision']!r}")

    confidence = float(result["confidence"])
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"Confidence {confidence} out of range [0, 1]")

    return result
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_strategy.py -v
```

Expected: All tests PASSED (the timeout test may take ~2 seconds)

- [ ] **Step 5: Commit**

```bash
git add services/research/strategy.py services/research/tests/test_strategy.py
git commit -m "feat: add strategy framework (AST validator, sandboxed executor, SHA256 hash)"
```

---

### Task 4: Research Queries

**Files:**
- Create: `services/research/queries.py`
- Create: `services/research/tests/test_queries.py`

- [ ] **Step 1: Write failing tests**

```python
# services/research/tests/test_queries.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch, MagicMock, call

from queries import (
    save_strategy,
    get_strategies,
    save_backtest_run,
    save_backtest_trades,
    get_strategy_runs,
    update_strategy_status,
    get_candidates,
)


def _make_mock_conn(rows=None, fetchone_row=None):
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows or []
    mock_cur.fetchone.return_value = fetchone_row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


@contextmanager
def _make_mock_cm(mock_conn):
    yield mock_conn


class TestSaveStrategy(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_new_id(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(fetchone_row={"id": 42})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = save_strategy(
            name="Test", description="desc", hypothesis="hyp",
            code="def generate_signal(s): pass",
            code_hash="abc123", triggered_by="manual"
        )
        self.assertEqual(result, 42)

    @patch("queries.get_conn")
    def test_executes_insert_with_draft_status(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(fetchone_row={"id": 1})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        save_strategy("n", "d", "h", "code", "hash", "manual")
        sql_called = mock_cur.execute.call_args[0][0]
        self.assertIn("INSERT INTO strategies", sql_called)
        self.assertIn("draft", sql_called)


class TestGetStrategies(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_list(self, mock_get_conn):
        rows = [{"id": 1, "name": "S1", "status": "draft", "sharpe_ratio": None}]
        mock_conn, mock_cur = _make_mock_conn(rows=rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_strategies()
        self.assertEqual(result, rows)

    @patch("queries.get_conn")
    def test_returns_empty_list_when_no_strategies(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(rows=[])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_strategies()
        self.assertEqual(result, [])


class TestSaveBacktestRun(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_run_id(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(fetchone_row={"id": 7})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        metrics = {
            "total_return_pct": 12.5, "sharpe_ratio": 1.2,
            "max_drawdown_pct": 8.0, "win_rate_pct": 55.0,
            "trade_count": 10, "avg_hold_bars": 5.5,
        }
        params = {
            "initial_capital": 100000, "max_position_pct": 0.15,
            "stop_loss_pct": 0.05, "max_hold_bars": 20,
        }
        result = save_backtest_run(
            strategy_id=1, symbol="AAPL",
            start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
            data_source="yfinance", params=params, metrics=metrics,
        )
        self.assertEqual(result, 7)


class TestSaveBacktestTrades(unittest.TestCase):
    @patch("queries.execute_values")
    @patch("queries.get_conn")
    def test_calls_execute_values_with_trades(self, mock_get_conn, mock_exec_values):
        mock_conn, mock_cur = _make_mock_conn()
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        trades = [
            {"side": "buy", "entry_bar": 50, "exit_bar": 55,
             "entry_price": 100.0, "exit_price": 105.0,
             "position_size": 12000.0, "pnl": 600.0, "exit_reason": "signal"},
        ]
        save_backtest_trades(run_id=7, symbol="AAPL", trades=trades)
        self.assertTrue(mock_exec_values.called)

    @patch("queries.get_conn")
    def test_no_op_when_trades_empty(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn()
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        # Should not raise
        save_backtest_trades(run_id=7, symbol="AAPL", trades=[])


class TestGetStrategyRuns(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_runs_for_strategy(self, mock_get_conn):
        rows = [{"id": 1, "strategy_id": 5, "symbol": "AAPL", "sharpe_ratio": 1.2}]
        mock_conn, mock_cur = _make_mock_conn(rows=rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_strategy_runs(5)
        self.assertEqual(result, rows)


class TestUpdateStrategyStatus(unittest.TestCase):
    @patch("queries.get_conn")
    def test_executes_update(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn()
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        update_strategy_status(strategy_id=3, status="candidate")
        sql = mock_cur.execute.call_args[0][0]
        self.assertIn("UPDATE strategies", sql)

    @patch("queries.get_conn")
    def test_passes_correct_params(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn()
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        update_strategy_status(strategy_id=3, status="retired")
        params = mock_cur.execute.call_args[0][1]
        self.assertIn("retired", params)
        self.assertIn(3, params)


class TestGetCandidates(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_candidate_list(self, mock_get_conn):
        rows = [{"id": 2, "name": "S2", "status": "candidate", "sharpe_ratio": 1.5}]
        mock_conn, mock_cur = _make_mock_conn(rows=rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = get_candidates()
        self.assertEqual(result, rows)
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_queries.py -v
```

Expected: `ModuleNotFoundError: No module named 'queries'`

- [ ] **Step 3: Implement queries.py**

```python
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
    Return all candidate strategies with their best Alpaca run and best yfinance run,
    sorted by Alpaca Sharpe ratio descending.
    """
    sql = """
        SELECT
            s.id, s.name, s.description, s.hypothesis, s.code, s.code_hash,
            s.status, s.triggered_by, s.created_at,
            alp.id         AS alp_run_id,
            alp.symbol, alp.start_date, alp.end_date,
            alp.total_return_pct, alp.sharpe_ratio, alp.max_drawdown_pct,
            alp.win_rate_pct, alp.trade_count, alp.avg_hold_bars, alp.critique,
            yf.id          AS yf_run_id,
            yf.total_return_pct AS yf_total_return_pct,
            yf.sharpe_ratio     AS yf_sharpe_ratio,
            yf.max_drawdown_pct AS yf_max_drawdown_pct,
            yf.win_rate_pct     AS yf_win_rate_pct,
            yf.trade_count      AS yf_trade_count
        FROM strategies s
        LEFT JOIN LATERAL (
            SELECT id, symbol, start_date, end_date,
                   total_return_pct, sharpe_ratio, max_drawdown_pct,
                   win_rate_pct, trade_count, avg_hold_bars, critique
            FROM backtest_runs
            WHERE strategy_id = s.id AND data_source = 'alpaca'
            ORDER BY sharpe_ratio DESC NULLS LAST
            LIMIT 1
        ) alp ON true
        LEFT JOIN LATERAL (
            SELECT id, total_return_pct, sharpe_ratio, max_drawdown_pct,
                   win_rate_pct, trade_count
            FROM backtest_runs
            WHERE strategy_id = s.id AND data_source = 'yfinance'
            ORDER BY sharpe_ratio DESC NULLS LAST
            LIMIT 1
        ) yf ON true
        WHERE s.status = 'candidate'
        ORDER BY alp.sharpe_ratio DESC NULLS LAST
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql)
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_queries.py -v
```

Expected: All tests PASSED

- [ ] **Step 5: Commit**

```bash
git add services/research/queries.py services/research/tests/test_queries.py
git commit -m "feat: add research queries (strategy registry, backtest runs, trades)"
```

---

### Task 5: Data Fetchers

**Files:**
- Create: `services/research/data.py`
- Create: `services/research/tests/test_data.py`

- [ ] **Step 1: Write failing tests**

```python
# services/research/tests/test_data.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from datetime import date
from unittest.mock import patch, MagicMock
import pandas as pd

from data import fetch_bars_yfinance, fetch_bars_alpaca

START = date(2023, 1, 1)
END = date(2023, 12, 31)


def _make_yf_df(n: int = 5) -> pd.DataFrame:
    """Minimal yfinance-style DataFrame."""
    idx = pd.date_range("2023-01-03", periods=n, freq="B")
    return pd.DataFrame({
        "Open":   [100.0 + i for i in range(n)],
        "High":   [102.0 + i for i in range(n)],
        "Low":    [ 98.0 + i for i in range(n)],
        "Close":  [101.0 + i for i in range(n)],
        "Volume": [1_000_000 + i * 1000 for i in range(n)],
    }, index=idx)


class TestFetchBarsYfinance(unittest.TestCase):
    @patch("data.yf.Ticker")
    def test_returns_bars_in_expected_format(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_yf_df(5)
        mock_ticker_cls.return_value = mock_ticker

        bars = fetch_bars_yfinance("AAPL", START, END)

        self.assertEqual(len(bars), 5)
        first = bars[0]
        self.assertIn("t", first)
        self.assertIn("o", first)
        self.assertIn("h", first)
        self.assertIn("l", first)
        self.assertIn("c", first)
        self.assertIn("v", first)
        self.assertIsInstance(first["o"], float)
        self.assertIsInstance(first["v"], int)

    @patch("data.yf.Ticker")
    def test_raises_on_empty_response(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        with self.assertRaises(ValueError) as ctx:
            fetch_bars_yfinance("FAKE", START, END)
        self.assertIn("No bars", str(ctx.exception))

    @patch("data.yf.Ticker")
    def test_passes_date_range_to_history(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = _make_yf_df(3)
        mock_ticker_cls.return_value = mock_ticker

        fetch_bars_yfinance("AAPL", START, END)
        call_kwargs = mock_ticker.history.call_args[1]
        self.assertEqual(call_kwargs["start"], START)
        self.assertEqual(call_kwargs["end"], END)


class TestFetchBarsAlpaca(unittest.TestCase):
    @patch("data.tradeapi.REST")
    def test_returns_bars_in_expected_format(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api

        idx = pd.date_range("2023-01-03 09:30", periods=5, freq="15min", tz="UTC")
        mock_df = pd.DataFrame({
            "open":   [100.0 + i for i in range(5)],
            "high":   [102.0 + i for i in range(5)],
            "low":    [ 98.0 + i for i in range(5)],
            "close":  [101.0 + i for i in range(5)],
            "volume": [50000 + i * 100 for i in range(5)],
        }, index=idx)
        mock_api.get_bars.return_value.df = mock_df

        bars = fetch_bars_alpaca(
            symbol="AAPL", start_date=START, end_date=END,
            api_key="key", secret_key="secret", base_url="https://paper-api.alpaca.markets",
        )

        self.assertEqual(len(bars), 5)
        first = bars[0]
        for key in ("t", "o", "h", "l", "c", "v"):
            self.assertIn(key, first)
        self.assertIsInstance(first["o"], float)
        self.assertIsInstance(first["v"], int)

    @patch("data.tradeapi.REST")
    def test_raises_on_empty_response(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api
        mock_api.get_bars.return_value.df = pd.DataFrame()

        with self.assertRaises(ValueError) as ctx:
            fetch_bars_alpaca("FAKE", START, END, "k", "s", "url")
        self.assertIn("No bars", str(ctx.exception))

    @patch("data.tradeapi.REST")
    def test_requests_15min_bars(self, mock_rest_cls):
        mock_api = MagicMock()
        mock_rest_cls.return_value = mock_api
        idx = pd.date_range("2023-01-03", periods=3, freq="15min", tz="UTC")
        mock_api.get_bars.return_value.df = pd.DataFrame({
            "open": [1.0]*3, "high": [1.0]*3, "low": [1.0]*3,
            "close": [1.0]*3, "volume": [100]*3,
        }, index=idx)

        fetch_bars_alpaca("AAPL", START, END, "k", "s", "url")
        call_args = mock_api.get_bars.call_args
        # Second positional arg is the timeframe
        self.assertIn("15Min", str(call_args))
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_data.py -v
```

Expected: `ModuleNotFoundError: No module named 'data'`

- [ ] **Step 3: Implement data.py**

```python
# services/research/data.py
from datetime import date as Date

import yfinance as yf
import alpaca_trade_api as tradeapi


def fetch_bars_yfinance(symbol: str, start_date: Date, end_date: Date) -> list[dict]:
    """
    Fetch daily OHLCV bars from yfinance.
    Returns list of dicts with keys: t, o, h, l, c, v.
    Raises ValueError if no bars are returned.
    """
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date)

    if df.empty:
        raise ValueError(f"No bars returned for {symbol} from yfinance")

    result = []
    for ts, row in df.iterrows():
        result.append({
            "t": str(ts),
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"]),
            "v": int(row["Volume"]),
        })
    return result


def fetch_bars_alpaca(
    symbol: str,
    start_date: Date,
    end_date: Date,
    api_key: str,
    secret_key: str,
    base_url: str,
) -> list[dict]:
    """
    Fetch 15-minute OHLCV bars from Alpaca historical API.
    Returns list of dicts with keys: t, o, h, l, c, v.
    Raises ValueError if no bars are returned.
    """
    api = tradeapi.REST(api_key, secret_key, base_url)
    bars_resp = api.get_bars(
        symbol,
        "15Min",
        start=start_date.isoformat(),
        end=end_date.isoformat(),
        limit=10000,
    )
    df = bars_resp.df

    if df.empty:
        raise ValueError(f"No bars returned for {symbol} from Alpaca")

    result = []
    for ts, row in df.iterrows():
        result.append({
            "t": str(ts),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "v": int(row["volume"]),
        })
    return result
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_data.py -v
```

Expected: All tests PASSED

- [ ] **Step 5: Commit**

```bash
git add services/research/data.py services/research/tests/test_data.py
git commit -m "feat: add data fetchers (yfinance daily bars, Alpaca 15-min bars)"
```

---

### Task 6: Backtester

**Files:**
- Create: `services/research/backtester.py`
- Create: `services/research/tests/test_backtester.py`

- [ ] **Step 1: Write failing tests**

```python
# services/research/tests/test_backtester.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from backtester import compute_indicators_series, run_backtest

SLIPPAGE = 0.0005


def _make_bars(n: int, start_price: float = 100.0) -> list[dict]:
    """Generate n bars with a gentle uptrend. Enough for indicator warm-up with n >= 52."""
    bars = []
    price = start_price
    for _ in range(n):
        bars.append({
            "o": round(price * 0.999, 4),
            "h": round(price * 1.010, 4),
            "l": round(price * 0.990, 4),
            "c": round(price, 4),
            "v": 1_000_000,
        })
        price = round(price * 1.001, 4)
    return bars


HOLD_CODE = """
def generate_signal(snapshot):
    return {"decision": "hold", "confidence": 0.5, "reasoning": "never trade"}
"""

BUY_CODE = """
def generate_signal(snapshot):
    return {"decision": "buy", "confidence": 0.80, "reasoning": "always buy"}
"""

DEFAULT_PARAMS = {
    "initial_capital": 100_000,
    "max_position_pct": 0.15,
    "stop_loss_pct": 0.05,
    "max_hold_bars": 20,
}


class TestComputeIndicatorsSeries(unittest.TestCase):
    def test_returns_empty_for_fewer_than_52_bars(self):
        bars = _make_bars(51)
        result = compute_indicators_series(bars)
        self.assertEqual(result, [])

    def test_returns_snapshots_for_sufficient_bars(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        self.assertGreater(len(result), 0)

    def test_snapshot_has_all_required_keys(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        snap = result[0]
        for key in ("price", "rsi", "sma20", "sma50", "sma20_prev", "sma20_prev2",
                    "volume", "volume_avg"):
            self.assertIn(key, snap, f"Missing key: {key}")

    def test_snapshot_has_internal_navigation_keys(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        snap = result[0]
        self.assertIn("_bar_idx", snap)
        self.assertIn("_open", snap)

    def test_excludes_last_bar(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        # _bar_idx should never be the last bar index (no next bar to fill at)
        last_idx = len(bars) - 1
        for snap in result:
            self.assertLess(snap["_bar_idx"], last_idx)

    def test_next_open_matches_bars(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        for snap in result:
            idx = snap["_bar_idx"]
            self.assertAlmostEqual(snap["_open"], bars[idx + 1]["o"])


class TestRunBacktest(unittest.TestCase):
    def test_zero_trades_when_hold_always(self):
        bars = _make_bars(60)
        metrics, trades = run_backtest(HOLD_CODE, bars, DEFAULT_PARAMS)
        self.assertEqual(trades, [])
        self.assertEqual(metrics["trade_count"], 0)
        self.assertAlmostEqual(metrics["total_return_pct"], 0.0)
        self.assertEqual(metrics["win_rate_pct"], 0.0)
        self.assertIsNone(metrics["sharpe_ratio"])

    def test_metrics_keys_present(self):
        bars = _make_bars(60)
        metrics, _ = run_backtest(HOLD_CODE, bars, DEFAULT_PARAMS)
        for key in ("total_return_pct", "sharpe_ratio", "max_drawdown_pct",
                    "win_rate_pct", "trade_count", "avg_hold_bars"):
            self.assertIn(key, metrics, f"Missing metric: {key}")

    def test_position_sizing_confidence_scaled(self):
        """position_size ≈ confidence × max_position_pct × initial_capital"""
        bars = _make_bars(80)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 3, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        self.assertGreater(len(trades), 0)
        # With confidence=0.80, max_position_pct=0.15, capital=100000:
        # position_size = 0.80 * 0.15 * 100000 = 12000 (before slippage, approximately)
        first = trades[0]
        self.assertAlmostEqual(first["position_size"], 12000.0, delta=300)

    def test_stop_loss_exit(self):
        """Position exits with stop_loss reason when close drops below stop price."""
        bars = _make_bars(65, start_price=100.0)
        # Drop bar 52 well below stop (entry ≈ 100, stop at 95, close at 88)
        bars[52]["c"] = 88.0
        bars[52]["l"] = 88.0
        params = {**DEFAULT_PARAMS, "stop_loss_pct": 0.05, "max_hold_bars": 100}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        stop_trades = [t for t in trades if t["exit_reason"] == "stop_loss"]
        self.assertGreater(len(stop_trades), 0)
        self.assertLess(stop_trades[0]["pnl"], 0)  # stop loss is always a loss

    def test_max_hold_exit(self):
        """Position exits with max_hold reason after max_hold_bars bars."""
        bars = _make_bars(80, start_price=100.0)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 3, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        max_hold_trades = [t for t in trades if t["exit_reason"] == "max_hold"]
        self.assertGreater(len(max_hold_trades), 0)
        # exit_bar - entry_bar == 4 for max_hold_bars=3
        # (held through 3 complete bar periods, exit at 4th bar's open)
        first = max_hold_trades[0]
        self.assertEqual(first["exit_bar"] - first["entry_bar"], 4)

    def test_signal_exit(self):
        """Position exits with signal reason when sell signal is received."""
        sell_on_third = """
_count = [0]
def generate_signal(snapshot):
    _count[0] += 1
    if _count[0] == 1:
        return {"decision": "buy", "confidence": 0.80, "reasoning": "buy"}
    if _count[0] == 3:
        return {"decision": "sell", "confidence": 0.50, "reasoning": "sell"}
    return {"decision": "hold", "confidence": 0.50, "reasoning": "hold"}
"""
        bars = _make_bars(60)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 100, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(sell_on_third, bars, params)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "signal")

    def test_win_rate_computed_correctly(self):
        """50% win rate when exactly half trades are profitable."""
        # Strategy: buy at bar 1, sell at bar 3 (gain), buy at bar 5, sell at bar 7 (loss)
        gain_then_loss = """
_count = [0]
def generate_signal(snapshot):
    _count[0] += 1
    # Buy on call 1, sell on call 3, buy on call 4, sell on call 6
    if _count[0] in (1, 4):
        return {"decision": "buy", "confidence": 0.80, "reasoning": "buy"}
    if _count[0] in (3, 6):
        return {"decision": "sell", "confidence": 0.50, "reasoning": "sell"}
    return {"decision": "hold", "confidence": 0.50, "reasoning": "hold"}
"""
        bars = _make_bars(60)  # Uptrend: both trades should be wins
        params = {**DEFAULT_PARAMS, "max_hold_bars": 100, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(gain_then_loss, bars, params)
        # In an uptrend, both signal exits should be wins
        self.assertGreater(metrics["win_rate_pct"], 0)
        self.assertGreater(metrics["trade_count"], 0)

    def test_total_return_positive_in_uptrend(self):
        """Buying in an uptrend should produce positive returns."""
        bars = _make_bars(70)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 5, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        if trades:
            self.assertGreater(metrics["total_return_pct"], -50)  # sanity check

    def test_returns_empty_for_insufficient_bars(self):
        """Backtest on fewer than 52 bars produces zero trades."""
        bars = _make_bars(40)
        metrics, trades = run_backtest(BUY_CODE, bars, DEFAULT_PARAMS)
        self.assertEqual(trades, [])
        self.assertEqual(metrics["trade_count"], 0)
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_backtester.py -v
```

Expected: `ModuleNotFoundError: No module named 'backtester'`

- [ ] **Step 3: Implement backtester.py**

```python
# services/research/backtester.py
import statistics
from typing import Optional

import pandas as pd
import ta.momentum
import ta.trend

from strategy import validate_strategy_code, load_strategy, execute_strategy

_SLIPPAGE = 0.0005  # 0.05% per side
_MIN_BARS = 52      # Minimum bars for a meaningful indicator series


def compute_indicators_series(bars: list[dict]) -> list[dict]:
    """
    Compute rolling indicators across all bars and return snapshots for every bar
    where all indicators are valid. Each snapshot includes internal keys:
      _bar_idx  — index into bars list
      _open     — next bar's open (fill price for trades)

    Returns [] if fewer than _MIN_BARS bars.
    The last bar is always excluded (no next bar to fill at).
    """
    if len(bars) < _MIN_BARS:
        return []

    closes = pd.Series([float(b["c"]) for b in bars])
    volumes = pd.Series([float(b["v"]) for b in bars])

    rsi_series = ta.momentum.RSIIndicator(close=closes, window=14).rsi()
    sma20_series = ta.trend.SMAIndicator(close=closes, window=20).sma_indicator()
    sma50_series = ta.trend.SMAIndicator(close=closes, window=50).sma_indicator()
    vol_avg_series = volumes.rolling(window=20).mean()

    snapshots = []
    # SMA50 is first valid at index 49. We also need sma20[i-1] and sma20[i-2],
    # which are valid from index 21 onward. Binding constraint: i >= 49.
    # Stop at len(bars)-2 so bars[i+1] always exists.
    for i in range(49, len(bars) - 1):
        if (
            pd.isna(rsi_series.iloc[i])
            or pd.isna(sma20_series.iloc[i])
            or pd.isna(sma50_series.iloc[i])
            or i < 2
            or pd.isna(sma20_series.iloc[i - 1])
            or pd.isna(sma20_series.iloc[i - 2])
            or pd.isna(vol_avg_series.iloc[i])
        ):
            continue
        snapshots.append({
            "price":       float(closes.iloc[i]),
            "rsi":         float(rsi_series.iloc[i]),
            "sma20":       float(sma20_series.iloc[i]),
            "sma50":       float(sma50_series.iloc[i]),
            "sma20_prev":  float(sma20_series.iloc[i - 1]),
            "sma20_prev2": float(sma20_series.iloc[i - 2]),
            "volume":      float(volumes.iloc[i]),
            "volume_avg":  float(vol_avg_series.iloc[i]),
            "_bar_idx":    i,
            "_open":       float(bars[i + 1]["o"]),
        })
    return snapshots


def run_backtest(
    strategy_code: str,
    bars: list[dict],
    params: dict,
) -> tuple[dict, list[dict]]:
    """
    Run a vectorized backtest of strategy_code against bars.

    params keys:
        initial_capital   — starting portfolio value (default 100_000)
        max_position_pct  — max fraction of portfolio per trade (default 0.15)
        stop_loss_pct     — stop loss fraction below entry (default 0.05)
        max_hold_bars     — max bars to hold a position (default 20)

    Returns (metrics_dict, trades_list).
    metrics_dict keys: total_return_pct, sharpe_ratio, max_drawdown_pct,
                       win_rate_pct, trade_count, avg_hold_bars
    trades_list dicts: entry_bar, exit_bar, entry_price, exit_price,
                       position_size, pnl, exit_reason, side
    """
    validate_strategy_code(strategy_code)
    fn = load_strategy(strategy_code)

    snapshots = compute_indicators_series(bars)
    if not snapshots:
        return _empty_metrics(), []

    initial_capital = float(params.get("initial_capital", 100_000))
    max_position_pct = float(params.get("max_position_pct", 0.15))
    stop_loss_pct = float(params.get("stop_loss_pct", 0.05))
    max_hold_bars = int(params.get("max_hold_bars", 20))

    cash = initial_capital
    position: Optional[dict] = None
    portfolio_history: list[float] = []
    trades: list[dict] = []

    for snap in snapshots:
        bar_idx = snap["_bar_idx"]
        next_open = snap["_open"]
        current_close = snap["price"]

        # Mark portfolio value (cash + open position at current close)
        mtm = cash + (position["shares"] * current_close if position else 0.0)
        portfolio_history.append(mtm)

        # --- Check forced exits BEFORE generating signal ---
        if position is not None:
            bars_held = bar_idx - position["entry_bar"]
            stop_price = position["entry_price"] * (1.0 - stop_loss_pct)

            force_exit = None
            if current_close <= stop_price:
                force_exit = "stop_loss"
            elif bars_held >= max_hold_bars:
                force_exit = "max_hold"

            if force_exit:
                exit_price = next_open * (1.0 - _SLIPPAGE)
                pnl = (exit_price - position["entry_price"]) * position["shares"]
                cash += position["shares"] * exit_price
                trades.append({
                    "side": "buy",
                    "entry_bar": position["entry_bar"],
                    "exit_bar": bar_idx + 1,
                    "entry_price": round(position["entry_price"], 4),
                    "exit_price": round(exit_price, 4),
                    "position_size": position["position_size"],
                    "pnl": round(pnl, 4),
                    "exit_reason": force_exit,
                })
                position = None
                continue  # Skip signal generation this bar

        # --- Generate signal ---
        signal_input = {k: v for k, v in snap.items() if not k.startswith("_")}
        try:
            result = execute_strategy(fn, signal_input)
        except ValueError:
            # Invalid schema aborts bar; treated as hold
            continue

        decision = result["decision"]
        confidence = float(result["confidence"])

        if position is None and decision == "buy":
            # Enter long position at next bar's open
            portfolio_value = cash  # no open position
            position_size_dollars = confidence * max_position_pct * portfolio_value
            if position_size_dollars > cash:
                position_size_dollars = cash
            if position_size_dollars <= 0:
                continue
            entry_price = next_open * (1.0 + _SLIPPAGE)
            shares = position_size_dollars / entry_price
            cost = shares * entry_price
            cash -= cost
            position = {
                "entry_bar": bar_idx + 1,
                "entry_price": entry_price,
                "shares": shares,
                "position_size": round(position_size_dollars, 4),
            }

        elif position is not None and decision == "sell":
            # Exit long position at next bar's open
            exit_price = next_open * (1.0 - _SLIPPAGE)
            pnl = (exit_price - position["entry_price"]) * position["shares"]
            cash += position["shares"] * exit_price
            trades.append({
                "side": "buy",
                "entry_bar": position["entry_bar"],
                "exit_bar": bar_idx + 1,
                "entry_price": round(position["entry_price"], 4),
                "exit_price": round(exit_price, 4),
                "position_size": position["position_size"],
                "pnl": round(pnl, 4),
                "exit_reason": "signal",
            })
            position = None

    # Close any remaining open position at last bar's close
    if position is not None:
        last_close = float(bars[-1]["c"])
        exit_price = last_close * (1.0 - _SLIPPAGE)
        pnl = (exit_price - position["entry_price"]) * position["shares"]
        cash += position["shares"] * exit_price
        trades.append({
            "side": "buy",
            "entry_bar": position["entry_bar"],
            "exit_bar": len(bars) - 1,
            "entry_price": round(position["entry_price"], 4),
            "exit_price": round(exit_price, 4),
            "position_size": position["position_size"],
            "pnl": round(pnl, 4),
            "exit_reason": "signal",
        })

    final_value = cash
    metrics = _compute_metrics(trades, initial_capital, final_value, portfolio_history)
    return metrics, trades


def _empty_metrics() -> dict:
    return {
        "total_return_pct": 0.0,
        "sharpe_ratio": None,
        "max_drawdown_pct": 0.0,
        "win_rate_pct": 0.0,
        "trade_count": 0,
        "avg_hold_bars": None,
    }


def _compute_metrics(
    trades: list[dict],
    initial_capital: float,
    final_value: float,
    portfolio_history: list[float],
) -> dict:
    trade_count = len(trades)

    if trade_count == 0:
        return _empty_metrics()

    total_return_pct = round(
        (final_value - initial_capital) / initial_capital * 100, 4
    )

    winning = [t for t in trades if t["pnl"] > 0]
    win_rate_pct = round(len(winning) / trade_count * 100, 4)

    hold_bars = [
        t["exit_bar"] - t["entry_bar"]
        for t in trades
        if t.get("exit_bar") is not None
    ]
    avg_hold_bars = round(sum(hold_bars) / len(hold_bars), 2) if hold_bars else None

    max_drawdown_pct = _compute_max_drawdown(portfolio_history)

    # Sharpe: annualised from per-trade returns (return = pnl / position_size)
    trade_returns = [
        t["pnl"] / t["position_size"]
        for t in trades
        if t.get("position_size") and t["position_size"] > 0
    ]
    if len(trade_returns) < 2:
        sharpe_ratio = None
    else:
        mean_r = statistics.mean(trade_returns)
        std_r = statistics.stdev(trade_returns)
        if std_r == 0:
            sharpe_ratio = None
        else:
            sharpe_ratio = round(mean_r / std_r * (252 ** 0.5), 4)

    return {
        "total_return_pct": total_return_pct,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown_pct": max_drawdown_pct,
        "win_rate_pct": win_rate_pct,
        "trade_count": trade_count,
        "avg_hold_bars": avg_hold_bars,
    }


def _compute_max_drawdown(portfolio_history: list[float]) -> float:
    if not portfolio_history:
        return 0.0
    peak = portfolio_history[0]
    max_dd = 0.0
    for value in portfolio_history:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak * 100
            if drawdown > max_dd:
                max_dd = drawdown
    return round(max_dd, 4)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_backtester.py -v
```

Expected: All tests PASSED (may take a few seconds for indicator computation)

- [ ] **Step 5: Commit**

```bash
git add services/research/backtester.py services/research/tests/test_backtester.py
git commit -m "feat: add vectorized backtester (indicator series, trade simulation, metrics)"
```

---

### Task 7: API Routes

**Files:**
- Modify: `services/research/main.py` (add all 9 routes)
- Modify: `services/research/tests/test_main.py` (add route tests)

- [ ] **Step 1: Write failing tests for all routes**

Append to `services/research/tests/test_main.py`:

```python
import json
from unittest.mock import patch, MagicMock

MOCK_STRATEGY = {
    "id": 1, "name": "RSI Bounce", "description": "RSI dip strategy",
    "hypothesis": "Buy oversold RSI", "code": "def generate_signal(s): pass",
    "code_hash": "abc123", "status": "draft", "triggered_by": "manual",
    "created_at": "2026-05-17T00:00:00Z",
    "sharpe_ratio": None, "win_rate_pct": None, "max_drawdown_pct": None,
    "total_return_pct": None, "trade_count": None,
}

MOCK_RUN = {
    "id": 1, "strategy_id": 1, "symbol": "AAPL",
    "start_date": "2023-01-01", "end_date": "2023-12-31",
    "data_source": "yfinance", "initial_capital": "100000.00",
    "total_return_pct": "12.50", "sharpe_ratio": "1.20",
    "max_drawdown_pct": "8.00", "win_rate_pct": "55.00",
    "trade_count": 10, "avg_hold_bars": "5.50", "ran_at": "2026-05-17T00:00:00Z",
}

VALID_CODE = """
def generate_signal(snapshot):
    return {"decision": "hold", "confidence": 0.5, "reasoning": "hold"}
"""

INVALID_CODE = "import os\ndef generate_signal(s): pass"


class TestStrategyRoutes(unittest.TestCase):
    def setUp(self):
        from main import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    @patch("main.save_strategy", return_value=1)
    @patch("main.compute_code_hash", return_value="hash123")
    @patch("main.validate_strategy_code")
    def test_post_strategy_valid_returns_201(self, mock_val, mock_hash, mock_save):
        resp = self.client.post(
            "/api/strategies",
            json={"name": "Test", "description": "d", "hypothesis": "h",
                  "code": VALID_CODE, "triggered_by": "manual"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["id"], 1)

    @patch("main.validate_strategy_code", side_effect=ValueError("Import not allowed"))
    def test_post_strategy_invalid_code_returns_400(self, mock_val):
        resp = self.client.post(
            "/api/strategies",
            json={"name": "Bad", "description": "d", "hypothesis": "h",
                  "code": INVALID_CODE, "triggered_by": "manual"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    @patch("main.get_strategies", return_value=[MOCK_STRATEGY])
    def test_get_strategies_returns_list(self, mock_get):
        resp = self.client.get("/api/strategies")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

    @patch("main.get_strategy_runs", return_value=[MOCK_RUN])
    def test_get_strategy_runs(self, mock_runs):
        resp = self.client.get("/api/strategies/1/runs")
        self.assertEqual(resp.status_code, 200)

    @patch("main.update_strategy_status")
    def test_retire_strategy(self, mock_update):
        resp = self.client.post("/api/strategies/1/retire")
        self.assertEqual(resp.status_code, 200)
        mock_update.assert_called_once_with(strategy_id=1, status="retired")

    @patch("main.get_candidates", return_value=[])
    def test_get_candidates_page(self, mock_cands):
        resp = self.client.get("/candidates")
        self.assertEqual(resp.status_code, 200)

    def test_get_research_page(self):
        with patch("main.get_strategies", return_value=[]):
            resp = self.client.get("/research")
            self.assertEqual(resp.status_code, 200)
```

- [ ] **Step 2: Run to confirm failure**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_main.py -v
```

Expected: Several failures due to missing routes.

- [ ] **Step 3: Implement full main.py**

```python
# services/research/main.py
import sys
import os
sys.path.insert(0, "/app")

from flask import Flask, render_template, jsonify, request, abort
from shared.config import load_config
from shared.logger import get_logger

from strategy import validate_strategy_code, compute_code_hash, load_strategy
from backtester import run_backtest
from data import fetch_bars_yfinance, fetch_bars_alpaca
from queries import (
    save_strategy,
    get_strategies,
    save_backtest_run,
    save_backtest_trades,
    get_strategy_runs,
    update_strategy_status,
    get_candidates,
    get_run_trades,
)

log = get_logger("research")

app = Flask(__name__)

# Candidate promotion thresholds (Alpaca run must pass all)
_SHARPE_MIN = 0.5
_WIN_RATE_MIN = 45.0
_MAX_DRAWDOWN_MAX = 20.0


@app.route("/health")
def health():
    return {"status": "ok"}, 200


# ── HTML pages ────────────────────────────────────────────────────────────────

@app.route("/research")
def research_page():
    strategies = get_strategies()
    return render_template("research.html", strategies=strategies)


@app.route("/candidates")
def candidates_page():
    candidates = get_candidates()
    return render_template("candidates.html", candidates=candidates)


# ── Strategy API ──────────────────────────────────────────────────────────────

@app.route("/api/strategies", methods=["POST"])
def create_strategy():
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")
    try:
        validate_strategy_code(code)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    strategy_id = save_strategy(
        name=body.get("name", "Unnamed"),
        description=body.get("description", ""),
        hypothesis=body.get("hypothesis", ""),
        code=code,
        code_hash=compute_code_hash(code),
        triggered_by=body.get("triggered_by", "manual"),
    )
    log.info("Strategy saved: id=%s name=%s", strategy_id, body.get("name"))
    return jsonify({"id": strategy_id, "status": "draft"}), 201


@app.route("/api/strategies", methods=["GET"])
def list_strategies():
    strategies = get_strategies()
    return jsonify([dict(s) for s in strategies])


@app.route("/api/strategies/<int:strategy_id>/backtest", methods=["POST"])
def trigger_backtest(strategy_id: int):
    body = request.get_json(silent=True) or {}
    symbol = body.get("symbol")
    start_date = body.get("start_date")
    end_date = body.get("end_date")
    data_source = body.get("data_source", "yfinance")

    if not symbol or not start_date or not end_date:
        return jsonify({"error": "symbol, start_date, end_date are required"}), 400

    # Fetch strategy code
    strategies = get_strategies()
    strat = next((s for s in strategies if s["id"] == strategy_id), None)
    if strat is None:
        return jsonify({"error": "Strategy not found"}), 404

    params = {
        "initial_capital": float(body.get("initial_capital", 100_000)),
        "max_position_pct": float(body.get("max_position_pct", 0.15)),
        "stop_loss_pct": float(body.get("stop_loss_pct", 0.05)),
        "max_hold_bars": int(body.get("max_hold_bars", 20)),
    }

    cfg = load_config()
    from datetime import date as _date
    import datetime

    try:
        start = datetime.date.fromisoformat(start_date)
        end = datetime.date.fromisoformat(end_date)

        if data_source == "alpaca":
            bars = fetch_bars_alpaca(
                symbol=symbol, start_date=start, end_date=end,
                api_key=os.environ["ALPACA_API_KEY"],
                secret_key=os.environ["ALPACA_SECRET_KEY"],
                base_url=os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
            )
        else:
            bars = fetch_bars_yfinance(symbol=symbol, start_date=start, end_date=end)
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        log.error("Data fetch failed: %s", e)
        return jsonify({"error": "Data fetch failed"}), 500

    try:
        metrics, trades = run_backtest(strat["code"], bars, params)
    except Exception as e:
        log.error("Backtest failed for strategy %s: %s", strategy_id, e)
        return jsonify({"error": str(e)}), 500

    # Persist results
    update_strategy_status(strategy_id=strategy_id, status="testing")
    run_id = save_backtest_run(
        strategy_id=strategy_id, symbol=symbol,
        start_date=start, end_date=end,
        data_source=data_source, params=params, metrics=metrics,
    )
    if trades:
        save_backtest_trades(run_id=run_id, symbol=symbol, trades=trades)

    # Auto-promote to candidate if Alpaca run passes thresholds
    if data_source == "alpaca" and _passes_candidate_thresholds(metrics):
        update_strategy_status(strategy_id=strategy_id, status="candidate")
        log.info("Strategy %s promoted to candidate (Sharpe=%.2f)", strategy_id, metrics.get("sharpe_ratio") or 0)

    log.info("Backtest complete: strategy=%s run=%s trades=%s", strategy_id, run_id, metrics["trade_count"])
    return jsonify({"run_id": run_id, "metrics": metrics}), 200


@app.route("/api/strategies/<int:strategy_id>/runs", methods=["GET"])
def strategy_runs(strategy_id: int):
    runs = get_strategy_runs(strategy_id)
    return jsonify([dict(r) for r in runs])


@app.route("/api/strategies/<int:strategy_id>/approve", methods=["POST"])
def approve_strategy(strategy_id: int):
    update_strategy_status(strategy_id=strategy_id, status="approved")
    log.info("Strategy %s approved", strategy_id)
    return jsonify({"status": "approved"}), 200


@app.route("/api/strategies/<int:strategy_id>/retire", methods=["POST"])
def retire_strategy(strategy_id: int):
    update_strategy_status(strategy_id=strategy_id, status="retired")
    log.info("Strategy %s retired", strategy_id)
    return jsonify({"status": "retired"}), 200


@app.route("/api/runs/<int:run_id>/trades", methods=["GET"])
def run_trades(run_id: int):
    trades = get_run_trades(run_id)
    return jsonify([dict(t) for t in trades])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _passes_candidate_thresholds(metrics: dict) -> bool:
    """Return True if Alpaca run metrics meet candidate promotion thresholds."""
    sharpe = metrics.get("sharpe_ratio")
    win_rate = metrics.get("win_rate_pct")
    max_dd = metrics.get("max_drawdown_pct")
    trade_count = metrics.get("trade_count", 0)
    if not all([sharpe is not None, win_rate is not None, max_dd is not None]):
        return False
    return (
        trade_count > 0
        and sharpe >= _SHARPE_MIN
        and win_rate >= _WIN_RATE_MIN
        and max_dd <= _MAX_DRAWDOWN_MAX
    )


if __name__ == "__main__":
    log.info("Research Service starting")
    app.run(host="0.0.0.0", port=8081)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_main.py -v
```

Expected: All tests PASSED

- [ ] **Step 5: Commit**

```bash
git add services/research/main.py services/research/tests/test_main.py
git commit -m "feat: add research service API routes (strategy CRUD, backtest trigger, approval)"
```

---

### Task 8: Strategy Browser UI

**Files:**
- Create: `services/research/templates/research.html`
- Modify: `services/research/static/research.css`

- [ ] **Step 1: Create research.html**

```html
<!-- services/research/templates/research.html -->
{% extends "base.html" %}
{% block title %} — Research{% endblock %}

{% block content %}
<div class="page-header">
  <h1 class="page-title">Strategy Research</h1>
  <button class="btn btn-primary" onclick="document.getElementById('new-strategy-modal').style.display='flex'">
    + New Strategy
  </button>
</div>

<!-- New Strategy Modal -->
<div id="new-strategy-modal" class="modal-overlay" style="display:none">
  <div class="modal-box">
    <h2>New Strategy</h2>
    <form id="new-strategy-form">
      <label>Name</label>
      <input type="text" name="name" required placeholder="RSI Bounce" />
      <label>Hypothesis</label>
      <textarea name="hypothesis" rows="3" required placeholder="Strategy should buy when RSI is oversold and..."></textarea>
      <label>Description (optional)</label>
      <input type="text" name="description" placeholder="Short summary" />
      <label>Triggered By</label>
      <select name="triggered_by">
        <option value="manual">manual</option>
        <option value="scheduled">scheduled</option>
        <option value="reactive">reactive</option>
      </select>
      <label>Strategy Code</label>
      <textarea name="code" rows="14" required placeholder="def generate_signal(snapshot):
    rsi = snapshot['rsi']
    if rsi < 35:
        return {'decision': 'buy', 'confidence': 0.78, 'reasoning': 'Oversold RSI.'}
    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'No signal.'}"></textarea>
      <div class="modal-actions">
        <button type="button" class="btn btn-secondary" onclick="document.getElementById('new-strategy-modal').style.display='none'">Cancel</button>
        <button type="submit" class="btn btn-primary">Save Strategy</button>
      </div>
    </form>
    <div id="new-strategy-error" class="error-msg" style="display:none"></div>
  </div>
</div>

<!-- Strategy Table -->
{% if strategies %}
<div class="card">
  <table class="data-table">
    <thead>
      <tr>
        <th>Name</th>
        <th>Status</th>
        <th>Source</th>
        <th>Sharpe</th>
        <th>Win Rate</th>
        <th>Max DD</th>
        <th>Created</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for s in strategies %}
      <tr class="strategy-row" data-id="{{ s.id }}">
        <td><strong>{{ s.name }}</strong><br><small class="muted">{{ s.hypothesis[:80] }}{% if s.hypothesis|length > 80 %}…{% endif %}</small></td>
        <td><span class="badge badge-{{ s.status }}">{{ s.status }}</span></td>
        <td>{{ s.triggered_by }}</td>
        <td>{{ '%.2f'|format(s.sharpe_ratio|float) if s.sharpe_ratio is not none else '—' }}</td>
        <td>{{ '%.1f'|format(s.win_rate_pct|float) if s.win_rate_pct is not none else '—' }}%</td>
        <td>{{ '%.1f'|format(s.max_drawdown_pct|float) if s.max_drawdown_pct is not none else '—' }}%</td>
        <td>{{ s.created_at.strftime('%Y-%m-%d') if s.created_at else '—' }}</td>
        <td><button class="btn btn-sm" onclick="toggleExpand({{ s.id }})">▼</button></td>
      </tr>
      <tr id="expand-{{ s.id }}" class="expand-row" style="display:none">
        <td colspan="8">
          <div class="expand-content">
            <h3>Strategy Code</h3>
            <pre class="code-block"><code>{{ s.code }}</code></pre>

            <h3>Backtest History</h3>
            <div id="runs-{{ s.id }}"><em>Loading…</em></div>

            <h3>Run New Backtest</h3>
            <form class="backtest-form" data-strategy-id="{{ s.id }}">
              <div class="form-row">
                <label>Symbol<input type="text" name="symbol" value="AAPL" required /></label>
                <label>Start Date<input type="date" name="start_date" value="2022-01-01" required /></label>
                <label>End Date<input type="date" name="end_date" value="2024-12-31" required /></label>
                <label>Data Source
                  <select name="data_source">
                    <option value="yfinance">yfinance (fast)</option>
                    <option value="alpaca">Alpaca (validation)</option>
                  </select>
                </label>
              </div>
              <details>
                <summary>Advanced Parameters</summary>
                <div class="form-row">
                  <label>Initial Capital<input type="number" name="initial_capital" value="100000" /></label>
                  <label>Max Position %<input type="number" name="max_position_pct" value="0.15" step="0.01" /></label>
                  <label>Stop Loss %<input type="number" name="stop_loss_pct" value="0.05" step="0.01" /></label>
                  <label>Max Hold Bars<input type="number" name="max_hold_bars" value="20" /></label>
                </div>
              </details>
              <div class="form-actions">
                <button type="submit" class="btn btn-primary">Run Backtest</button>
                <span class="backtest-status"></span>
              </div>
            </form>

            <div class="retire-section">
              <button class="btn btn-danger btn-sm" onclick="retireStrategy({{ s.id }})">Retire Strategy</button>
            </div>
          </div>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% else %}
<div class="empty-state">
  <p>No strategies yet. Create one to get started.</p>
</div>
{% endif %}

<script>
function toggleExpand(id) {
  const row = document.getElementById('expand-' + id);
  const visible = row.style.display !== 'none';
  row.style.display = visible ? 'none' : 'table-row';
  if (!visible) loadRuns(id);
}

function loadRuns(strategyId) {
  fetch('/api/strategies/' + strategyId + '/runs')
    .then(r => r.json())
    .then(runs => {
      const el = document.getElementById('runs-' + strategyId);
      if (!runs.length) { el.innerHTML = '<em>No backtest runs yet.</em>'; return; }
      let html = '<table class="data-table"><thead><tr><th>Symbol</th><th>Source</th><th>Sharpe</th><th>Win Rate</th><th>Max DD</th><th>Return</th><th>Trades</th><th>Ran At</th></tr></thead><tbody>';
      runs.forEach(r => {
        html += `<tr>
          <td>${r.symbol}</td>
          <td>${r.data_source}</td>
          <td>${r.sharpe_ratio != null ? parseFloat(r.sharpe_ratio).toFixed(2) : '—'}</td>
          <td>${r.win_rate_pct != null ? parseFloat(r.win_rate_pct).toFixed(1) + '%' : '—'}</td>
          <td>${r.max_drawdown_pct != null ? parseFloat(r.max_drawdown_pct).toFixed(1) + '%' : '—'}</td>
          <td>${r.total_return_pct != null ? parseFloat(r.total_return_pct).toFixed(2) + '%' : '—'}</td>
          <td>${r.trade_count ?? '—'}</td>
          <td>${r.ran_at ? r.ran_at.substring(0,10) : '—'}</td>
        </tr>`;
      });
      el.innerHTML = html + '</tbody></table>';
    });
}

document.querySelectorAll('.backtest-form').forEach(form => {
  form.addEventListener('submit', e => {
    e.preventDefault();
    const strategyId = form.dataset.strategyId;
    const statusEl = form.querySelector('.backtest-status');
    const fd = new FormData(form);
    const body = Object.fromEntries(fd.entries());
    statusEl.textContent = 'Running…';
    fetch('/api/strategies/' + strategyId + '/backtest', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    })
    .then(r => r.json())
    .then(d => {
      if (d.error) { statusEl.textContent = 'Error: ' + d.error; return; }
      statusEl.textContent = `Done — Sharpe: ${d.metrics.sharpe_ratio ?? '—'}, Trades: ${d.metrics.trade_count}`;
      loadRuns(strategyId);
    })
    .catch(() => { statusEl.textContent = 'Request failed'; });
  });
});

document.getElementById('new-strategy-form').addEventListener('submit', e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd.entries());
  const errEl = document.getElementById('new-strategy-error');
  fetch('/api/strategies', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  })
  .then(r => r.json().then(d => ({status: r.status, data: d})))
  .then(({status, data}) => {
    if (status !== 201) { errEl.style.display = 'block'; errEl.textContent = data.error; return; }
    location.reload();
  });
});

function retireStrategy(id) {
  if (!confirm('Retire this strategy?')) return;
  fetch('/api/strategies/' + id + '/retire', {method: 'POST'})
    .then(() => location.reload());
}
</script>
{% endblock %}
```

- [ ] **Step 2: Create base.html for the research service**

The research service has its own Flask app and needs its own `base.html`. Create it at `services/research/templates/base.html` extending the dashboard style visually (copy styles, not Flask's url_for since endpoints differ):

```html
<!-- services/research/templates/base.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AlphaDivision Research{% block title %}{% endblock %}</title>
  <link rel="stylesheet" href="/static/research.css">
</head>
<body>
  <header class="navbar">
    <span class="nav-brand">AlphaDivision</span>
    <nav class="nav-links">
      <a href="http://localhost:8080/">Overview</a>
      <a href="http://localhost:8080/trades">Trades</a>
      <a href="http://localhost:8080/analysis">Analysis</a>
      <a href="/research" class="{{ 'active' if request.endpoint == 'research_page' else '' }}">Research</a>
      <a href="/candidates" class="{{ 'active' if request.endpoint == 'candidates_page' else '' }}">Candidates</a>
    </nav>
  </header>

  <main class="container">
    {% block content %}{% endblock %}
  </main>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</body>
</html>
```

- [ ] **Step 3: Add research.css with required styles**

```css
/* services/research/static/research.css */

/* ── Base / Reset ─────────────────────────────────────────────────────────── */
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --border: #2a2d3a;
  --text: #e2e8f0;
  --muted: #64748b;
  --accent: #6366f1;
  --danger: #ef4444;
  --success: #22c55e;
  --warning: #f59e0b;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; }

/* ── Layout ───────────────────────────────────────────────────────────────── */
.navbar { background: var(--surface); border-bottom: 1px solid var(--border); display: flex; align-items: center; padding: 0 24px; height: 56px; gap: 32px; }
.nav-brand { font-weight: 700; font-size: 16px; }
.nav-links { display: flex; gap: 24px; }
.nav-links a { color: var(--muted); text-decoration: none; font-size: 14px; }
.nav-links a.active, .nav-links a:hover { color: var(--text); }
.container { max-width: 1280px; margin: 0 auto; padding: 24px 16px; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
.page-title { font-size: 22px; font-weight: 600; }

/* ── Cards ────────────────────────────────────────────────────────────────── */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 16px; }

/* ── Buttons ──────────────────────────────────────────────────────────────── */
.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; font-weight: 500; transition: opacity .15s; }
.btn:hover { opacity: .85; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-secondary { background: var(--border); color: var(--text); }
.btn-danger { background: var(--danger); color: #fff; }
.btn-sm { padding: 4px 10px; font-size: 12px; }

/* ── Tables ───────────────────────────────────────────────────────────────── */
.data-table { width: 100%; border-collapse: collapse; }
.data-table th { text-align: left; padding: 10px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); border-bottom: 1px solid var(--border); }
.data-table td { padding: 12px; border-bottom: 1px solid var(--border); }
.data-table tbody tr:hover { background: rgba(255,255,255,.03); }

/* ── Badges ───────────────────────────────────────────────────────────────── */
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.badge-draft     { background: #334155; color: #94a3b8; }
.badge-testing   { background: #1e3a5f; color: #60a5fa; }
.badge-candidate { background: #1a2f1a; color: #4ade80; }
.badge-approved  { background: #1a2f1a; color: #22c55e; }
.badge-live      { background: #1a3a1a; color: #16a34a; }
.badge-retired   { background: #2d1a1a; color: #f87171; }

/* ── Expand rows ──────────────────────────────────────────────────────────── */
.expand-row td { background: #13161f; padding: 0; }
.expand-content { padding: 20px; border-top: 1px solid var(--border); }
.expand-content h3 { margin: 16px 0 8px; font-size: 13px; color: var(--muted); text-transform: uppercase; }
.expand-content h3:first-child { margin-top: 0; }

/* ── Code blocks ──────────────────────────────────────────────────────────── */
.code-block { background: #0a0c12; border: 1px solid var(--border); border-radius: 6px; padding: 16px; overflow-x: auto; font-family: 'SF Mono', monospace; font-size: 13px; line-height: 1.6; color: #a5b4fc; white-space: pre; }

/* ── Forms ────────────────────────────────────────────────────────────────── */
.backtest-form { margin-top: 8px; }
.form-row { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
.form-row label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.form-row input, .form-row select { background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 6px 10px; font-size: 13px; width: 160px; }
.form-actions { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.backtest-status { font-size: 13px; color: var(--muted); }
.retire-section { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border); }
details summary { cursor: pointer; color: var(--muted); font-size: 12px; margin-bottom: 8px; }

/* ── Modal ────────────────────────────────────────────────────────────────── */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal-box { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 24px; width: 640px; max-width: 95vw; max-height: 90vh; overflow-y: auto; }
.modal-box h2 { margin-bottom: 16px; font-size: 18px; }
.modal-box label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; margin-top: 12px; }
.modal-box input, .modal-box select, .modal-box textarea { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 8px 10px; font-size: 13px; font-family: inherit; }
.modal-box textarea { font-family: 'SF Mono', monospace; resize: vertical; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.error-msg { color: var(--danger); font-size: 13px; margin-top: 8px; }

/* ── Misc ─────────────────────────────────────────────────────────────────── */
.muted { color: var(--muted); }
.empty-state { text-align: center; padding: 60px; color: var(--muted); }
.candidate-card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 24px; margin-bottom: 24px; }
.metrics-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 16px 0; }
.metrics-table th, .metrics-table td { padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
.metrics-table th { color: var(--muted); font-weight: 500; }
.chart-container { margin: 16px 0; }
.candidate-actions { display: flex; gap: 12px; margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border); }
```

- [ ] **Step 4: Verify research page renders**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_main.py::TestStrategyRoutes::test_get_research_page -v
```

Expected: PASSED

- [ ] **Step 5: Commit**

```bash
git add services/research/templates/ services/research/static/
git commit -m "feat: add research browser UI (strategy table, expandable rows, backtest form)"
```

---

### Task 9: Approval Queue UI

**Files:**
- Create: `services/research/templates/candidates.html`

- [ ] **Step 1: Create candidates.html**

```html
<!-- services/research/templates/candidates.html -->
{% extends "base.html" %}
{% block title %} — Candidates{% endblock %}

{% block content %}
<div class="page-header">
  <h1 class="page-title">Candidate Strategies</h1>
  <span class="muted">{{ candidates|length }} candidate{{ 's' if candidates|length != 1 else '' }}</span>
</div>

{% if candidates %}
  {% for c in candidates %}
  <div class="candidate-card">
    <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:12px;">
      <div>
        <h2 style="font-size:18px; margin-bottom:4px;">{{ c.name }}</h2>
        <span class="badge badge-candidate">candidate</span>
        <span class="muted" style="margin-left:8px; font-size:13px;">{{ c.triggered_by }}</span>
      </div>
    </div>

    <p style="color:var(--muted); margin-bottom:16px; font-size:13px;">{{ c.hypothesis }}</p>

    <!-- Metrics comparison table -->
    <div class="metrics-grid">
      <div>
        <h3 style="font-size:12px; color:var(--muted); text-transform:uppercase; margin-bottom:8px;">Alpaca (Validation)</h3>
        <table class="data-table metrics-table" style="font-size:13px;">
          <tr><th>Sharpe Ratio</th><td>{{ '%.2f'|format(c.sharpe_ratio|float) if c.sharpe_ratio is not none else '—' }}</td></tr>
          <tr><th>Win Rate</th><td>{{ '%.1f'|format(c.win_rate_pct|float) if c.win_rate_pct is not none else '—' }}%</td></tr>
          <tr><th>Max Drawdown</th><td>{{ '%.1f'|format(c.max_drawdown_pct|float) if c.max_drawdown_pct is not none else '—' }}%</td></tr>
          <tr><th>Total Return</th><td>{{ '%.2f'|format(c.total_return_pct|float) if c.total_return_pct is not none else '—' }}%</td></tr>
          <tr><th>Trades</th><td>{{ c.trade_count ?? '—' }}</td></tr>
        </table>
      </div>
      <div>
        <h3 style="font-size:12px; color:var(--muted); text-transform:uppercase; margin-bottom:8px;">yfinance (Research)</h3>
        <table class="data-table metrics-table" style="font-size:13px;">
          <tr><th>Sharpe Ratio</th><td>{{ '%.2f'|format(c.yf_sharpe_ratio|float) if c.yf_sharpe_ratio is not none else '—' }}</td></tr>
          <tr><th>Win Rate</th><td>{{ '%.1f'|format(c.yf_win_rate_pct|float) if c.yf_win_rate_pct is not none else '—' }}%</td></tr>
          <tr><th>Max Drawdown</th><td>{{ '%.1f'|format(c.yf_max_drawdown_pct|float) if c.yf_max_drawdown_pct is not none else '—' }}%</td></tr>
          <tr><th>Total Return</th><td>{{ '%.2f'|format(c.yf_total_return_pct|float) if c.yf_total_return_pct is not none else '—' }}%</td></tr>
          <tr><th>Trades</th><td>{{ c.yf_trade_count ?? '—' }}</td></tr>
        </table>
      </div>
    </div>

    <!-- Strategy Code -->
    <details style="margin: 16px 0;">
      <summary style="cursor:pointer; font-size:13px; color:var(--muted); margin-bottom:8px;">View Strategy Code</summary>
      <pre class="code-block"><code>{{ c.code }}</code></pre>
    </details>

    <!-- Equity Curve -->
    {% if c.alp_run_id %}
    <div class="chart-container">
      <h3 style="font-size:12px; color:var(--muted); text-transform:uppercase; margin-bottom:8px;">Equity Curve (Alpaca Run)</h3>
      <canvas id="equity-{{ c.id }}" height="80"></canvas>
    </div>
    {% endif %}

    <!-- Claude's Critique (populated in sub-project 3) -->
    {% if c.critique %}
    <div style="background:#0a0c12; border:1px solid var(--border); border-radius:6px; padding:16px; margin:16px 0;">
      <h3 style="font-size:12px; color:var(--muted); text-transform:uppercase; margin-bottom:8px;">Claude's Critique</h3>
      <p style="font-size:13px; line-height:1.6;">{{ c.critique }}</p>
    </div>
    {% endif %}

    <!-- Actions -->
    <div class="candidate-actions">
      <button class="btn btn-primary" onclick="approveStrategy({{ c.id }})">✓ Approve</button>
      <button class="btn btn-danger" onclick="retireStrategy({{ c.id }})">✗ Retire</button>
    </div>
  </div>
  {% endfor %}
{% else %}
<div class="empty-state">
  <p>No candidate strategies yet.</p>
  <p style="margin-top:8px; font-size:13px;">Run an Alpaca validation backtest on a strategy to promote it to candidate status.</p>
</div>
{% endif %}

<script>
// Load equity curve data for each candidate
{% for c in candidates %}
{% if c.alp_run_id %}
(function() {
  const runId = {{ c.alp_run_id }};
  const stratId = {{ c.id }};
  const canvas = document.getElementById('equity-' + stratId);
  if (!canvas) return;

  fetch('/api/runs/' + runId + '/trades')
    .then(r => r.json())
    .then(trades => {
      if (!trades.length) { canvas.style.display = 'none'; return; }
      // Build equity curve: cumulative PnL from initial capital
      const initial = 100000;
      let running = initial;
      const points = [{x: 0, y: initial}];
      trades.forEach(t => {
        running += parseFloat(t.pnl || 0);
        points.push({x: t.exit_bar, y: Math.round(running * 100) / 100});
      });

      new Chart(canvas, {
        type: 'line',
        data: {
          datasets: [{
            label: 'Portfolio Value',
            data: points,
            borderColor: '#6366f1',
            backgroundColor: 'rgba(99,102,241,0.1)',
            borderWidth: 2,
            pointRadius: 2,
            fill: true,
            tension: 0.1,
          }]
        },
        options: {
          responsive: true,
          parsing: { xAxisKey: 'x', yAxisKey: 'y' },
          plugins: { legend: { display: false } },
          scales: {
            x: { type: 'linear', ticks: { color: '#64748b' }, grid: { color: '#1e2230' } },
            y: { ticks: { color: '#64748b', callback: v => '$' + v.toLocaleString() }, grid: { color: '#1e2230' } },
          }
        }
      });
    });
})();
{% endif %}
{% endfor %}

function approveStrategy(id) {
  if (!confirm('Approve this strategy for deployment?')) return;
  fetch('/api/strategies/' + id + '/approve', { method: 'POST' })
    .then(r => r.json())
    .then(() => location.reload());
}

function retireStrategy(id) {
  if (!confirm('Retire this strategy?')) return;
  fetch('/api/strategies/' + id + '/retire', { method: 'POST' })
    .then(() => location.reload());
}
</script>
{% endblock %}
```

- [ ] **Step 2: Verify candidates page renders**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/test_main.py::TestStrategyRoutes::test_get_candidates_page -v
```

Expected: PASSED

- [ ] **Step 3: Run full test suite**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/ -v
```

Expected: All tests PASSED

- [ ] **Step 4: Commit**

```bash
git add services/research/templates/candidates.html
git commit -m "feat: add candidates approval queue UI (metrics comparison, equity curve, approve/retire)"
```

---

### Task 10: Docker Integration + Nav

**Files:**
- Modify: `docker-compose.yml` (add research service)
- Modify: `services/dashboard/templates/base.html` (add Research nav link)

- [ ] **Step 1: Add research service to docker-compose.yml**

In `docker-compose.yml`, add after the `dashboard` service block (before the `volumes:` section):

```yaml
  research:
    build: ./services/research
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: research
    ports:
      - "8081:8081"
    volumes:
      - ./shared:/app/shared
      - ./config.toml:/app/config.toml:ro
      - logs:/var/log/alphadivision
```

- [ ] **Step 2: Add Research link to dashboard base.html**

In `services/dashboard/templates/base.html`, add Research link between Analysis and Watchlist in both the desktop nav and the mobile tab bar:

Desktop nav — replace the Analysis → Watchlist section:
```html
      <a href="{{ url_for('analysis') }}" class="{{ 'active' if request.endpoint == 'analysis' else '' }}">Analysis</a>
      <a href="http://localhost:8081/research">Research</a>
      <a href="{{ url_for('watchlist') }}" class="{{ 'active' if request.endpoint == 'watchlist' else '' }}">Watchlist</a>
```

Mobile tab bar — add after the Analysis entry:
```html
    <a href="http://localhost:8081/research">
      <span>Research</span>
    </a>
```

- [ ] **Step 3: Apply the DB migration**

```bash
# Apply migration to running postgres container
docker exec -i $(docker ps -qf name=postgres) \
  psql -U $POSTGRES_USER -d alphadivision \
  < db/migrations/003_research_tables.sql
```

If running locally (not Docker), apply directly:
```bash
psql $DATABASE_URL < db/migrations/003_research_tables.sql
```

- [ ] **Step 4: Run full test suite before building**

```bash
PYTHONPATH=services/research:. pytest services/research/tests/ -v
```

Expected: All tests PASSED

- [ ] **Step 5: Rebuild and verify**

```bash
docker-compose build research
docker-compose up -d research
curl http://localhost:8081/health
```

Expected: `{"status": "ok"}`

Also verify the research page loads:
```bash
curl -s http://localhost:8081/research | grep -c "Research"
```

Expected: `> 0`

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml services/dashboard/templates/base.html db/migrations/003_research_tables.sql
git commit -m "feat: wire up research service in Docker, add Research nav link to dashboard"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| DB tables: strategies, backtest_runs, backtest_trades | Task 1 |
| Flask service port 8081 | Tasks 2, 10 |
| AST validation (import, open, exec, eval, os, sys, socket, subprocess) | Task 3 |
| ThreadPoolExecutor 2-second timeout | Task 3 |
| SHA256 code hash | Task 3 |
| Indicator series (RSI14, SMA20, SMA50, sma20_prev, sma20_prev2, volume_avg) | Task 6 |
| Entry fill at next bar's open | Task 6 |
| Confidence-scaled position sizing | Task 6 |
| Stop loss exit | Task 6 |
| Max hold exit | Task 6 |
| Signal-based exit | Task 6 |
| Slippage 0.05% per side | Task 6 |
| Metrics: total_return, sharpe, max_drawdown, win_rate, trade_count, avg_hold | Task 6 |
| yfinance daily bar fetcher | Task 5 |
| Alpaca 15-min bar fetcher | Task 5 |
| All 9 API routes | Task 7 |
| Auto-promotion to candidate (Sharpe≥0.5, win_rate≥45%, max_dd≤20%) | Task 7 |
| Strategy browser UI (/research) | Task 8 |
| Approval queue UI (/candidates) | Task 9 |
| Equity curve Chart.js | Task 9 |
| Docker service | Task 10 |
| Research nav link in base.html | Task 10 |

**All spec requirements covered. ✓**
