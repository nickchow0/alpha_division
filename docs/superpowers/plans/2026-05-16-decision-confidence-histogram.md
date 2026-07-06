# Decision Confidence Histogram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/analysis` page to the dashboard with a confidence distribution histogram, acted-on rate by band, win rate by confidence band, and a summary stat row — all filterable by time window.

**Architecture:** Four new SQL query functions in `queries.py`, two new Flask routes in `main.py` (`GET /analysis` and `GET /api/analysis?days=30|90|all`), one new template `analysis.html`, and nav additions in `base.html`. All data aggregation happens in PostgreSQL; Chart.js renders the results. Time window switching fires a fetch to `/api/analysis` and re-renders charts in place — no page reload.

**Tech Stack:** Python 3.11, Flask, psycopg2, PostgreSQL (`WIDTH_BUCKET`, `generate_series`, `PERCENTILE_CONT`, `LATERAL JOIN`), Chart.js 4.4.0 (already loaded in base.html).

---

## File Map

| File | Change |
|---|---|
| `services/dashboard/queries.py` | Add 4 functions: `get_analysis_stats`, `get_confidence_histogram`, `get_acted_on_rate_by_band`, `get_win_rate_by_band` |
| `services/dashboard/main.py` | Add imports, `_analysis_data()` helper, `/analysis` route, `/api/analysis` route; add `request` to Flask imports |
| `services/dashboard/templates/base.html` | Add "Analysis" link to desktop navbar and mobile tab-bar |
| `services/dashboard/templates/analysis.html` | New template: time selector, stat grid, 3 chart cards, Chart.js JS |
| `services/dashboard/static/style.css` | Add `.time-filter` and `.filter-btn` styles |
| `services/dashboard/tests/test_queries.py` | Add 4 test classes: `TestGetAnalysisStats`, `TestGetConfidenceHistogram`, `TestGetActedOnRateByBand`, `TestGetWinRateByBand` |
| `services/dashboard/tests/test_main.py` | Add mock constants, new patches to `setUp`, 3 new test methods |

---

## Background: Bucketing

All three charts use 20 buckets of 5% each. PostgreSQL's `WIDTH_BUCKET(LEAST(confidence::numeric, 0.9999), 0, 1, 20)` returns integers 1–20. The `LEAST()` guard handles `confidence = 1.0` (which would otherwise return 21). The 0.65 threshold falls exactly on the boundary between buckets 13 and 14:

- Buckets 1–13 (0–65%): styled gray `#8b949e`
- Buckets 14–20 (65–100%): styled blue `#58a6ff`

---

## Background: Test Helpers (already in test_queries.py)

The existing test file provides `_make_mock_conn(rows, fetchone_row=None)` and `_make_mock_cm(mock_conn)`. Use them throughout — don't redefine them.

```python
def _make_mock_conn(rows, fetchone_row=None):
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows
    mock_cur.fetchone.return_value = fetchone_row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur

@contextmanager
def _make_mock_cm(mock_conn):
    yield mock_conn
```

---

## Task 1: `get_analysis_stats` — tests then implementation

**Files:**
- Modify: `services/dashboard/tests/test_queries.py`
- Modify: `services/dashboard/queries.py`

- [ ] **Step 1: Add import and write failing tests**

Add to the top of `services/dashboard/tests/test_queries.py`:
```python
from queries import (
    get_open_positions,
    get_total_pnl,
    get_daily_pnl_today,
    get_recent_trades,
    get_recent_decisions,
    get_api_health,
    get_watchlist,
    get_circuit_breaker_status,
    get_pnl_history,
    get_trade_activity,
    get_trade_stats,
    get_analysis_stats,          # ← add
    get_confidence_histogram,    # ← add
    get_acted_on_rate_by_band,   # ← add
    get_win_rate_by_band,        # ← add
)
```

Then add this class at the end of `services/dashboard/tests/test_queries.py`:

```python
class TestGetAnalysisStats(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_zeros_when_no_decisions(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 0,
            "median_confidence": None,
            "pct_above_threshold": None,
            "pct_acted_on": None,
            "haiku_count": 0,
            "sonnet_count": 0,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_analysis_stats()
        self.assertEqual(result["total_decisions"], 0)
        self.assertEqual(result["median_confidence"], 0.0)
        self.assertEqual(result["pct_above_threshold"], 0.0)
        self.assertEqual(result["pct_acted_on"], 0.0)

    @patch("queries.get_conn")
    def test_all_keys_present(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 50,
            "median_confidence": "0.72",
            "pct_above_threshold": "68.0",
            "pct_acted_on": "45.0",
            "haiku_count": 40,
            "sonnet_count": 10,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_analysis_stats()
        for key in ("total_decisions", "median_confidence", "pct_above_threshold",
                    "pct_acted_on", "haiku_count", "sonnet_count"):
            self.assertIn(key, result)

    @patch("queries.get_conn")
    def test_median_confidence_is_float(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 10,
            "median_confidence": "0.71",
            "pct_above_threshold": "70.0",
            "pct_acted_on": "50.0",
            "haiku_count": 8,
            "sonnet_count": 2,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_analysis_stats()
        self.assertIsInstance(result["median_confidence"], float)

    @patch("queries.get_conn")
    def test_passes_days_param_when_provided(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 0, "median_confidence": None,
            "pct_above_threshold": None, "pct_acted_on": None,
            "haiku_count": 0, "sonnet_count": 0,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_analysis_stats(days=30)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(30, params)

    @patch("queries.get_conn")
    def test_no_params_when_days_is_none(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([], fetchone_row={
            "total_decisions": 0, "median_confidence": None,
            "pct_above_threshold": None, "pct_acted_on": None,
            "haiku_count": 0, "sonnet_count": 0,
        })
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_analysis_stats(days=None)
        params = mock_cur.execute.call_args[0][1]
        self.assertEqual(params, ())
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_queries.py::TestGetAnalysisStats -v
```

Expected: `ERROR` — `ImportError: cannot import name 'get_analysis_stats'`

- [ ] **Step 3: Implement `get_analysis_stats` in queries.py**

Add at the end of `services/dashboard/queries.py`:

```python
def get_analysis_stats(days=None) -> dict:
    """
    Aggregate summary stats for AI decisions within the given time window.

    Parameters:
        days: number of days to look back (None = all time, omits date filter)

    Returns a dict with keys:
        total_decisions: int
        median_confidence: float (0.0 when no decisions)
        pct_above_threshold: float — % of decisions with confidence >= 0.65
        pct_acted_on: float — % of decisions where acted_on is True
        haiku_count: int
        sonnet_count: int
    """
    date_clause = "AND decided_at >= NOW() - (%s * INTERVAL '1 day')" if days is not None else ""
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
            COUNT(*) FILTER (WHERE model LIKE '%haiku%')                      AS haiku_count,
            COUNT(*) FILTER (WHERE model LIKE '%sonnet%')                     AS sonnet_count
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
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_queries.py::TestGetAnalysisStats -v
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add services/dashboard/queries.py services/dashboard/tests/test_queries.py
git commit -m "feat(dashboard): add get_analysis_stats query"
```

---

## Task 2: `get_confidence_histogram` — tests then implementation

**Files:**
- Modify: `services/dashboard/tests/test_queries.py`
- Modify: `services/dashboard/queries.py`

- [ ] **Step 1: Add failing tests**

Add at the end of `services/dashboard/tests/test_queries.py`:

```python
class TestGetConfidenceHistogram(unittest.TestCase):
    def _make_20_rows(self):
        return [
            {"bucket": i, "label": f"{(i-1)*5}-{i*5}%", "count": 0}
            for i in range(1, 21)
        ]

    @patch("queries.get_conn")
    def test_always_returns_20_rows(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_confidence_histogram()
        self.assertEqual(len(result), 20)

    @patch("queries.get_conn")
    def test_bucket_13_label_is_60_65(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_confidence_histogram()
        # bucket 13 (index 12) covers 60-65%
        self.assertEqual(result[12]["label"], "60-65%")

    @patch("queries.get_conn")
    def test_bucket_14_label_is_65_70(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_confidence_histogram()
        self.assertEqual(result[13]["label"], "65-70%")

    @patch("queries.get_conn")
    def test_passes_days_param_when_provided(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_confidence_histogram(days=30)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(30, params)

    @patch("queries.get_conn")
    def test_no_params_when_days_is_none(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_confidence_histogram(days=None)
        params = mock_cur.execute.call_args[0][1]
        self.assertEqual(params, ())
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_queries.py::TestGetConfidenceHistogram -v
```

Expected: `ERROR` — `ImportError: cannot import name 'get_confidence_histogram'`

- [ ] **Step 3: Implement `get_confidence_histogram` in queries.py**

Add after `get_analysis_stats` in `services/dashboard/queries.py`:

```python
def get_confidence_histogram(days=None) -> list:
    """
    Return decision counts in 20 confidence buckets of 5% each (0-5%, 5-10%, ..., 95-100%).

    Always returns exactly 20 rows including zero-count buckets (via generate_series left join).
    Buckets 1-13 are below the 0.65 acting threshold; buckets 14-20 are above.

    Parameters:
        days: number of days to look back (None = all time)

    Each row: {"bucket": int (1-20), "label": str (e.g. "60-65%"), "count": int}
    """
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
            (((s.n - 1) * 5)::text || '-' || (s.n * 5)::text || '%')       AS label,
            COALESCE(b.count, 0)                                             AS count
        FROM generate_series(1, 20) s(n)
        LEFT JOIN buckets b ON b.bucket = s.n
        ORDER BY s.n
    """
    params = (days,) if days is not None else ()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_queries.py::TestGetConfidenceHistogram -v
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add services/dashboard/queries.py services/dashboard/tests/test_queries.py
git commit -m "feat(dashboard): add get_confidence_histogram query"
```

---

## Task 3: `get_acted_on_rate_by_band` — tests then implementation

**Files:**
- Modify: `services/dashboard/tests/test_queries.py`
- Modify: `services/dashboard/queries.py`

- [ ] **Step 1: Add failing tests**

Add at the end of `services/dashboard/tests/test_queries.py`:

```python
class TestGetActedOnRateByBand(unittest.TestCase):
    def _make_20_rows(self):
        return [
            {"bucket": i, "label": f"{(i-1)*5}-{i*5}%", "total": 0, "acted": 0, "acted_pct": 0}
            for i in range(1, 21)
        ]

    @patch("queries.get_conn")
    def test_returns_20_rows(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_acted_on_rate_by_band()
        self.assertEqual(len(result), 20)

    @patch("queries.get_conn")
    def test_acted_pct_is_float(self, mock_get_conn):
        rows = [{"bucket": 14, "label": "65-70%", "total": 10, "acted": 7, "acted_pct": "70.0"}]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_acted_on_rate_by_band()
        self.assertIsInstance(result[0]["acted_pct"], float)

    @patch("queries.get_conn")
    def test_zero_total_gives_zero_pct(self, mock_get_conn):
        rows = [{"bucket": 5, "label": "20-25%", "total": 0, "acted": 0, "acted_pct": 0}]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_acted_on_rate_by_band()
        self.assertEqual(result[0]["acted_pct"], 0.0)

    @patch("queries.get_conn")
    def test_passes_days_param_when_provided(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(self._make_20_rows())
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_acted_on_rate_by_band(days=90)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(90, params)
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_queries.py::TestGetActedOnRateByBand -v
```

Expected: `ERROR` — `ImportError: cannot import name 'get_acted_on_rate_by_band'`

- [ ] **Step 3: Implement `get_acted_on_rate_by_band` in queries.py**

Add after `get_confidence_histogram` in `services/dashboard/queries.py`:

```python
def get_acted_on_rate_by_band(days=None) -> list:
    """
    Return the acted-on rate per confidence band (20 buckets of 5%).

    Always returns exactly 20 rows including buckets with zero decisions.

    Parameters:
        days: number of days to look back (None = all time)

    Each row: {"bucket": int, "label": str, "total": int, "acted": int, "acted_pct": float}
    acted_pct is 0.0 when total == 0.
    """
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
            (((s.n - 1) * 5)::text || '-' || (s.n * 5)::text || '%')       AS label,
            COALESCE(b.total, 0)                                             AS total,
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
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_queries.py::TestGetActedOnRateByBand -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add services/dashboard/queries.py services/dashboard/tests/test_queries.py
git commit -m "feat(dashboard): add get_acted_on_rate_by_band query"
```

---

## Task 4: `get_win_rate_by_band` — tests then implementation

**Files:**
- Modify: `services/dashboard/tests/test_queries.py`
- Modify: `services/dashboard/queries.py`

- [ ] **Step 1: Add failing tests**

Add at the end of `services/dashboard/tests/test_queries.py`:

```python
class TestGetWinRateByBand(unittest.TestCase):
    @patch("queries.get_conn")
    def test_returns_empty_list_when_no_closed_trades(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_win_rate_by_band()
        self.assertEqual(result, [])

    @patch("queries.get_conn")
    def test_returns_only_buckets_with_sample_data(self, mock_get_conn):
        rows = [
            {"bucket": 14, "label": "65-70%", "sample_size": 5, "wins": 3, "win_rate_pct": "60.0"},
            {"bucket": 15, "label": "70-75%", "sample_size": 3, "wins": 2, "win_rate_pct": "66.7"},
        ]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_win_rate_by_band()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["bucket"], 14)

    @patch("queries.get_conn")
    def test_win_rate_pct_is_float(self, mock_get_conn):
        rows = [{"bucket": 14, "label": "65-70%", "sample_size": 5, "wins": 3, "win_rate_pct": "60.0"}]
        mock_conn, mock_cur = _make_mock_conn(rows)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        result = get_win_rate_by_band()
        self.assertIsInstance(result[0]["win_rate_pct"], float)

    @patch("queries.get_conn")
    def test_passes_days_param_when_provided(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn([])
        mock_get_conn.return_value = _make_mock_cm(mock_conn)
        get_win_rate_by_band(days=90)
        params = mock_cur.execute.call_args[0][1]
        self.assertIn(90, params)
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_queries.py::TestGetWinRateByBand -v
```

Expected: `ERROR` — `ImportError: cannot import name 'get_win_rate_by_band'`

- [ ] **Step 3: Implement `get_win_rate_by_band` in queries.py**

Add after `get_acted_on_rate_by_band` in `services/dashboard/queries.py`:

```python
def get_win_rate_by_band(days=None) -> list:
    """
    Return win rate per confidence band for closed trades (matched buy+sell pairs).

    Uses the same LATERAL join pattern as get_trade_stats(): each sell is matched
    to the most recent filled buy for the same symbol before the sell's filled_at.
    Safe because the bot holds at most one open position per symbol at a time.

    Parameters:
        days: number of days to look back based on decision timestamp (None = all time)

    Returns only buckets where sample_size > 0. Returns [] when no closed trades exist.
    Each row: {"bucket": int, "label": str, "sample_size": int, "wins": int, "win_rate_pct": float}
    """
    date_clause = "AND d.decided_at >= NOW() - (%s * INTERVAL '1 day')" if days is not None else ""
    sql = f"""
        WITH closed AS (
            SELECT
                d.confidence,
                (s.price - b.price) * s.qty > 0 AS is_win
            FROM decisions d
            JOIN signals sig ON sig.decision_id = d.id
            JOIN trades s ON s.signal_id = sig.id
                AND s.side = 'sell'
                AND s.status = 'filled'
                AND s.filled_at IS NOT NULL
            JOIN LATERAL (
                SELECT price FROM trades b
                WHERE b.symbol    = s.symbol
                  AND b.side      = 'buy'
                  AND b.status    = 'filled'
                  AND b.filled_at IS NOT NULL
                  AND b.filled_at < s.filled_at
                ORDER BY b.filled_at DESC
                LIMIT 1
            ) b ON true
            WHERE d.confidence IS NOT NULL
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
            (((bucket - 1) * 5)::text || '-' || (bucket * 5)::text || '%') AS label,
            sample_size,
            wins,
            ROUND(100.0 * wins / NULLIF(sample_size, 0), 1)                 AS win_rate_pct
        FROM bucketed
        WHERE sample_size > 0
        ORDER BY bucket
    """
    params = (days,) if days is not None else ()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall())
    return [{**dict(r), "win_rate_pct": float(r["win_rate_pct"] or 0)} for r in rows]
```

- [ ] **Step 4: Run all query tests — confirm all pass**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_queries.py -v
```

Expected: all tests pass (existing + 4 new classes)

- [ ] **Step 5: Commit**

```bash
git add services/dashboard/queries.py services/dashboard/tests/test_queries.py
git commit -m "feat(dashboard): add get_win_rate_by_band query"
```

---

## Task 5: Routes in main.py + route tests

**Files:**
- Modify: `services/dashboard/main.py`
- Modify: `services/dashboard/tests/test_main.py`

- [ ] **Step 1: Write failing route tests**

Add these constants near the top of `services/dashboard/tests/test_main.py` (after `MOCK_TRADE_STATS`):

```python
MOCK_ANALYSIS_STATS = {
    "total_decisions": 0,
    "median_confidence": 0.0,
    "pct_above_threshold": 0.0,
    "pct_acted_on": 0.0,
    "haiku_count": 0,
    "sonnet_count": 0,
}
MOCK_HISTOGRAM = [
    {"bucket": i, "label": f"{(i-1)*5}-{i*5}%", "count": 0}
    for i in range(1, 21)
]
MOCK_ACTED_ON_RATE = [
    {"bucket": i, "label": f"{(i-1)*5}-{i*5}%", "total": 0, "acted": 0, "acted_pct": 0.0}
    for i in range(1, 21)
]
```

Add these four patches to `self.patches` in `setUp` (before `import main`):

```python
patch("queries.get_analysis_stats", return_value=MOCK_ANALYSIS_STATS),
patch("queries.get_confidence_histogram", return_value=MOCK_HISTOGRAM),
patch("queries.get_acted_on_rate_by_band", return_value=MOCK_ACTED_ON_RATE),
patch("queries.get_win_rate_by_band", return_value=[]),
```

Add these test methods to `TestFlaskRoutes`:

```python
def test_analysis_returns_200(self):
    resp = self.client.get("/analysis")
    self.assertEqual(resp.status_code, 200)

def test_api_analysis_returns_200(self):
    resp = self.client.get("/api/analysis?days=30")
    self.assertEqual(resp.status_code, 200)
    data = resp.get_json()
    self.assertIn("stats", data)

def test_api_analysis_invalid_days_falls_back_to_30(self):
    resp = self.client.get("/api/analysis?days=bogus")
    self.assertEqual(resp.status_code, 200)
    data = resp.get_json()
    self.assertIn("stats", data)
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/test_main.py::TestFlaskRoutes::test_analysis_returns_200 -v
```

Expected: `FAILED` — `404 NOT FOUND` (route doesn't exist yet)

- [ ] **Step 3: Add imports and routes to main.py**

Update the Flask import line in `services/dashboard/main.py`:
```python
from flask import Flask, render_template, jsonify, request
```

Update the `from queries import (...)` block to add the four new functions:
```python
from queries import (
    get_open_positions,
    get_total_pnl,
    get_daily_pnl_today,
    get_recent_trades,
    get_recent_decisions,
    get_api_health,
    get_watchlist,
    get_circuit_breaker_status,
    get_pnl_history,
    get_trade_activity,
    get_trade_stats,
    get_analysis_stats,
    get_confidence_histogram,
    get_acted_on_rate_by_band,
    get_win_rate_by_band,
)
```

Add the helper function and two routes after the `api_charts` route (before `if __name__ == "__main__":`):

```python
def _analysis_data(days) -> dict:
    """Build template variables for the /analysis page."""
    stats = get_analysis_stats(days)
    histogram = get_confidence_histogram(days)
    acted_on_rate = get_acted_on_rate_by_band(days)
    win_rate = get_win_rate_by_band(days)
    return dict(
        stats=stats,
        hist_labels=json.dumps([r["label"] for r in histogram]),
        hist_counts=json.dumps([int(r["count"]) for r in histogram]),
        acted_pcts=json.dumps([float(r["acted_pct"]) for r in acted_on_rate]),
        win_labels=json.dumps([r["label"] for r in win_rate]),
        win_rates=json.dumps([float(r["win_rate_pct"]) for r in win_rate]),
    )


@app.route("/analysis")
def analysis():
    days = 30
    return render_template("analysis.html", active_days=days, **_analysis_data(days))


@app.route("/api/analysis")
def api_analysis():
    raw = request.args.get("days", "30")
    if raw == "all":
        days = None
    elif raw in ("30", "90"):
        days = int(raw)
    else:
        days = 30
    stats = get_analysis_stats(days)
    histogram = get_confidence_histogram(days)
    acted_on_rate = get_acted_on_rate_by_band(days)
    win_rate = get_win_rate_by_band(days)
    return jsonify(
        stats=stats,
        hist_labels=[r["label"] for r in histogram],
        hist_counts=[int(r["count"]) for r in histogram],
        acted_pcts=[float(r["acted_pct"]) for r in acted_on_rate],
        win_labels=[r["label"] for r in win_rate],
        win_rates=[float(r["win_rate_pct"]) for r in win_rate],
    )
```

- [ ] **Step 4: Run all tests — confirm all pass**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/ -v
```

Expected: all tests pass (existing 47 + 3 new route tests)

- [ ] **Step 5: Commit**

```bash
git add services/dashboard/main.py services/dashboard/tests/test_main.py
git commit -m "feat(dashboard): add /analysis and /api/analysis routes"
```

---

## Task 6: Nav, CSS, and analysis.html template

**Files:**
- Modify: `services/dashboard/templates/base.html`
- Modify: `services/dashboard/static/style.css`
- Create: `services/dashboard/templates/analysis.html`

- [ ] **Step 1: Add Analysis to the navbar in base.html**

In `services/dashboard/templates/base.html`, add the Analysis link after the Decisions link in the desktop navbar:

```html
<a href="{{ url_for('analysis') }}" class="{{ 'active' if request.endpoint == 'analysis' else '' }}">Analysis</a>
```

The desktop navbar block should look like this after the change:
```html
<nav class="nav-links">
  <a href="{{ url_for('overview') }}" class="{{ 'active' if request.endpoint == 'overview' else '' }}">Overview</a>
  <a href="{{ url_for('trades') }}" class="{{ 'active' if request.endpoint == 'trades' else '' }}">Trades</a>
  <a href="{{ url_for('decisions') }}" class="{{ 'active' if request.endpoint == 'decisions' else '' }}">Decisions</a>
  <a href="{{ url_for('analysis') }}" class="{{ 'active' if request.endpoint == 'analysis' else '' }}">Analysis</a>
  <a href="{{ url_for('watchlist') }}" class="{{ 'active' if request.endpoint == 'watchlist' else '' }}">Watchlist</a>
  <a href="{{ url_for('charts') }}" class="{{ 'active' if request.endpoint == 'charts' else '' }}">Charts</a>
</nav>
```

Also add to the mobile tab-bar after the Decisions entry:
```html
<a href="{{ url_for('analysis') }}" class="{{ 'active' if request.endpoint == 'analysis' else '' }}">
  <span>Analysis</span>
</a>
```

- [ ] **Step 2: Add time-filter button styles to style.css**

Find `services/dashboard/static/style.css` and append at the end:

```css
/* ── Analysis page: time filter toggle ────────────────────────────────────── */
.time-filter {
  display: flex;
  gap: 8px;
  margin-bottom: 20px;
}

.filter-btn {
  padding: 6px 16px;
  border: 1px solid #30363d;
  border-radius: 6px;
  background: #161b22;
  color: #8b949e;
  font-size: 0.85rem;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.filter-btn:hover {
  background: #21262d;
  color: #c9d1d9;
}

.filter-btn.active {
  background: #21262d;
  border-color: #58a6ff;
  color: #58a6ff;
}
```

- [ ] **Step 3: Create analysis.html**

Create `services/dashboard/templates/analysis.html` with this content:

```html
{% extends "base.html" %}
{% block title %} — Analysis{% endblock %}

{% block content %}
<div style="display:flex; align-items:baseline; justify-content:space-between; margin-bottom:16px;">
  <h1 style="margin-bottom:0">Decision Analysis</h1>
</div>

<!-- Time window selector -->
<div class="time-filter">
  <button class="filter-btn {{ 'active' if active_days == 30 else '' }}" data-days="30">30 days</button>
  <button class="filter-btn {{ 'active' if active_days == 90 else '' }}" data-days="90">90 days</button>
  <button class="filter-btn {{ 'active' if active_days is none else '' }}" data-days="all">All time</button>
</div>

<!-- Summary stat row -->
<div class="stat-grid" style="margin-bottom:24px;">
  <div class="stat-card">
    <span class="stat-label">Total Decisions</span>
    <span class="stat-value" id="stat-total-decisions">{{ stats.total_decisions }}</span>
  </div>
  <div class="stat-card">
    <span class="stat-label">Median Confidence</span>
    <span class="stat-value" id="stat-median-confidence">{{ "%.0f"|format(stats.median_confidence * 100) }}%</span>
  </div>
  <div class="stat-card">
    <span class="stat-label">Above Threshold</span>
    <span class="stat-value" id="stat-above-threshold">{{ "%.1f"|format(stats.pct_above_threshold) }}%</span>
  </div>
  <div class="stat-card">
    <span class="stat-label">Acted On</span>
    <span class="stat-value" id="stat-acted-on">{{ "%.1f"|format(stats.pct_acted_on) }}%</span>
  </div>
  <div class="stat-card">
    <span class="stat-label">Haiku</span>
    <span class="stat-value" id="stat-haiku">{{ stats.haiku_count }}</span>
  </div>
  <div class="stat-card">
    <span class="stat-label">Sonnet</span>
    <span class="stat-value" id="stat-sonnet">{{ stats.sonnet_count }}</span>
  </div>
</div>

<!-- Chart 1: Confidence Distribution -->
<div class="chart-card">
  <h2>Confidence Distribution</h2>
  <div class="chart-canvas-wrap"><canvas id="histChart"></canvas></div>
  <p id="histEmpty" class="empty-state" style="display:none">No decisions yet.</p>
</div>

<!-- Chart 2: Acted-on Rate by Band -->
<div class="chart-card">
  <h2>Acted-on Rate by Confidence Band</h2>
  <div class="chart-canvas-wrap"><canvas id="actedChart"></canvas></div>
  <p id="actedEmpty" class="empty-state" style="display:none">No decisions yet.</p>
</div>

<!-- Chart 3: Win Rate by Confidence Band -->
<div class="chart-card">
  <h2>Win Rate by Confidence Band</h2>
  <div class="chart-canvas-wrap"><canvas id="winChart"></canvas></div>
  <p id="winEmpty" class="empty-state" style="display:none">
    No closed trade data yet — check back after the first full trade cycle.
  </p>
</div>

<script>
(function () {
  /* ── Constants ── */
  const THRESHOLD_IDX = 13; // buckets 1-13 below threshold (index 0-12), 14-20 above (index 13-19)
  const GRAY  = '#8b949e';
  const BLUE  = '#58a6ff';
  const GREEN = '#3fb950';
  const RED   = '#f85149';

  const sharedOpts = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: '#30363d' }, ticks: { color: '#8b949e', maxRotation: 45, font: { size: 11 } } },
      y: { grid: { color: '#30363d' }, ticks: { color: '#8b949e' } }
    }
  };

  /* ── Color helpers ── */
  function histColors(n) {
    return Array.from({ length: n }, (_, i) => i < THRESHOLD_IDX ? GRAY : BLUE);
  }
  function winColors(rates) {
    return rates.map(r => r >= 50 ? GREEN : RED);
  }

  /* ── Show/hide canvas vs empty-state ── */
  function toggle(canvasId, emptyId, isEmpty) {
    document.getElementById(canvasId).style.display = isEmpty ? 'none' : 'block';
    document.getElementById(emptyId).style.display  = isEmpty ? 'block' : 'none';
  }

  /* ── Chart instances ── */
  let histChart, actedChart, winChart;

  /* ── Initial data from server ── */
  const initHistLabels  = {{ hist_labels | safe }};
  const initHistCounts  = {{ hist_counts | safe }};
  const initActedPcts   = {{ acted_pcts | safe }};
  const initWinLabels   = {{ win_labels | safe }};
  const initWinRates    = {{ win_rates | safe }};

  function initCharts(histLabels, histCounts, actedPcts, winLabels, winRates) {
    const histEmpty  = histCounts.every(c => c === 0);
    const actedEmpty = actedPcts.every(p => p === 0);
    const winEmpty   = winRates.length === 0;

    toggle('histChart',  'histEmpty',  histEmpty);
    toggle('actedChart', 'actedEmpty', actedEmpty);
    toggle('winChart',   'winEmpty',   winEmpty);

    if (!histEmpty) {
      histChart = new Chart(document.getElementById('histChart'), {
        type: 'bar',
        data: {
          labels: histLabels,
          datasets: [{ label: 'Decisions', data: histCounts,
            backgroundColor: histColors(histCounts.length) }]
        },
        options: sharedOpts
      });
    }

    if (!actedEmpty) {
      actedChart = new Chart(document.getElementById('actedChart'), {
        type: 'bar',
        data: {
          labels: histLabels,
          datasets: [{ label: 'Acted-on %', data: actedPcts,
            backgroundColor: histColors(actedPcts.length) }]
        },
        options: { ...sharedOpts, scales: { ...sharedOpts.scales,
          y: { ...sharedOpts.scales.y, min: 0, max: 100 } } }
      });
    }

    if (!winEmpty) {
      winChart = new Chart(document.getElementById('winChart'), {
        type: 'bar',
        data: {
          labels: winLabels,
          datasets: [{ label: 'Win Rate %', data: winRates,
            backgroundColor: winColors(winRates) }]
        },
        options: { ...sharedOpts, scales: { ...sharedOpts.scales,
          y: { ...sharedOpts.scales.y, min: 0, max: 100 } } }
      });
    }
  }

  function updateCharts(d) {
    const histEmpty  = d.hist_counts.every(c => c === 0);
    const actedEmpty = d.acted_pcts.every(p => p === 0);
    const winEmpty   = d.win_rates.length === 0;

    toggle('histChart',  'histEmpty',  histEmpty);
    toggle('actedChart', 'actedEmpty', actedEmpty);
    toggle('winChart',   'winEmpty',   winEmpty);

    if (!histEmpty && histChart) {
      histChart.data.labels = d.hist_labels;
      histChart.data.datasets[0].data = d.hist_counts;
      histChart.data.datasets[0].backgroundColor = histColors(d.hist_counts.length);
      histChart.update();
    }
    if (!actedEmpty && actedChart) {
      actedChart.data.datasets[0].data = d.acted_pcts;
      actedChart.update();
    }
    if (!winEmpty && winChart) {
      winChart.data.labels = d.win_labels;
      winChart.data.datasets[0].data = d.win_rates;
      winChart.data.datasets[0].backgroundColor = winColors(d.win_rates);
      winChart.update();
    }
  }

  function updateStats(s) {
    document.getElementById('stat-total-decisions').textContent = s.total_decisions;
    document.getElementById('stat-median-confidence').textContent =
      Math.round(s.median_confidence * 100) + '%';
    document.getElementById('stat-above-threshold').textContent =
      s.pct_above_threshold.toFixed(1) + '%';
    document.getElementById('stat-acted-on').textContent =
      s.pct_acted_on.toFixed(1) + '%';
    document.getElementById('stat-haiku').textContent = s.haiku_count;
    document.getElementById('stat-sonnet').textContent = s.sonnet_count;
  }

  /* ── Time filter ── */
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', async function () {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      this.classList.add('active');
      const days = this.dataset.days;
      try {
        const res = await fetch('/api/analysis?days=' + days);
        if (!res.ok) return;
        const d = await res.json();
        updateStats(d.stats);
        updateCharts(d);
      } catch (e) {
        console.warn('Analysis refresh failed:', e);
      }
    });
  });

  /* ── Initialize ── */
  initCharts(initHistLabels, initHistCounts, initActedPcts, initWinLabels, initWinRates);
})();
</script>
{% endblock %}
```

- [ ] **Step 4: Smoke test the template renders without error**

```bash
cd services/dashboard
PYTHONPATH=/Users/nickchow/claude/alphadivision:. python3 -m pytest tests/ -v
```

Expected: all tests still pass

- [ ] **Step 5: Commit**

```bash
git add services/dashboard/templates/base.html services/dashboard/static/style.css services/dashboard/templates/analysis.html
git commit -m "feat(dashboard): add /analysis page with confidence histogram and win rate charts"
```

---

## Task 7: Docker rebuild and smoke test

**Files:** None (infrastructure only)

- [ ] **Step 1: Rebuild the dashboard container**

```bash
cd /Users/nickchow/claude/alphadivision
docker compose up -d --build dashboard
```

Expected output ends with: `Container alphadivision-dashboard-1 Started`

- [ ] **Step 2: Confirm health endpoint**

```bash
curl -s http://localhost:8080/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 3: Confirm /analysis page loads**

```bash
curl -s http://localhost:8080/analysis | python3 -c "import sys; html=sys.stdin.read(); print('Analysis' in html, 'histChart' in html, 'winChart' in html)"
```

Expected: `True True True`

- [ ] **Step 4: Confirm /api/analysis returns valid JSON with expected keys**

```bash
curl -s "http://localhost:8080/api/analysis?days=30" | python3 -c "import sys, json; d=json.load(sys.stdin); print(sorted(d.keys()))"
```

Expected: `['acted_pcts', 'hist_counts', 'hist_labels', 'stats', 'win_labels', 'win_rates']`

- [ ] **Step 5: Confirm all-time window works**

```bash
curl -s "http://localhost:8080/api/analysis?days=all" | python3 -c "import sys, json; d=json.load(sys.stdin); print('ok' if 'stats' in d else 'fail')"
```

Expected: `ok`

- [ ] **Step 6: Push**

```bash
git push
```
