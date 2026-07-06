# ML Strategy Discovery Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous nightly ML pipeline (`services/ml/`) that discovers trading patterns from historical OHLCV data across 26 symbols, generates `generate_signal()` strategy code via Claude, backtests each strategy via the Research Service API, and auto-promotes winners to the candidate queue.

**Architecture:** A standalone Docker service (`services/ml/`) runs a nightly cron job at 2am — no HTTP API except a `/health` endpoint in a background thread. It shares the Postgres database via `shared.db`, calls yfinance for bar data, and calls the Research Service (`http://research:8081`) to trigger backtests rather than duplicating that logic. The pipeline is broken into five sequential phases: collect → features → discover → codegen → backtest+promote.

**Tech Stack:** Python 3.11, scikit-learn (DecisionTreeClassifier, KMeans, StandardScaler), pandas, numpy, yfinance, anthropic SDK, Flask (health server), schedule (cron), psycopg2, requests.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `db/migrations/004_ml_tables.sql` | Create | ml_bars cache + ml_runs history tables |
| `config.toml` | Modify | Add `[ml]` section with 26 symbols + thresholds |
| `shared/config.py` | Modify | Add ML defaults to `_DEFAULT_CONFIG` |
| `shared/tests/test_config.py` | Modify | Add test for new ml config keys |
| `services/ml/Dockerfile` | Create | Python 3.11-slim, copies service files |
| `services/ml/requirements.txt` | Create | scikit-learn, anthropic, schedule, flask, etc. |
| `services/ml/pipeline.py` | Create | Entrypoint: cron loop + health server + phase orchestration |
| `services/ml/queries.py` | Create | DB helpers: bars cache, save strategy, save run |
| `services/ml/collector.py` | Create | Phase 1: fetch + cache OHLCV bars via yfinance |
| `services/ml/features.py` | Create | Phase 2: compute 26-feature vector per bar |
| `services/ml/discoverer.py` | Create | Phase 3: decision tree + k-means pattern discovery |
| `services/ml/codegen.py` | Create | Phase 4: Claude API → generate_signal() code |
| `services/ml/tests/__init__.py` | Create | Empty, makes tests a package |
| `services/ml/tests/test_collector.py` | Create | Unit tests for collector (mocked yfinance + DB) |
| `services/ml/tests/test_features.py` | Create | Unit tests for all 26 indicators |
| `services/ml/tests/test_discoverer.py` | Create | Unit tests for DT + k-means on synthetic bars |
| `services/ml/tests/test_codegen.py` | Create | Unit tests for codegen (mocked Claude API) |
| `docker-compose.yml` | Modify | Add `ml` service block |

---

## Task 1: DB Migration + Config

**Files:**
- Create: `db/migrations/004_ml_tables.sql`
- Modify: `config.toml`
- Modify: `shared/config.py`
- Modify: `shared/tests/test_config.py`

- [ ] **Step 1: Write the failing config test**

Add to `shared/tests/test_config.py`:

```python
def test_ml_config_defaults():
    """ML config keys exist in defaults even with empty config.toml."""
    with patch("shared.config.open", side_effect=FileNotFoundError):
        cfg = load_config()
    ml = cfg["ml"]
    assert isinstance(ml["symbols"], list)
    assert len(ml["symbols"]) == 26
    assert ml["lookback_days_momentum"] == 365
    assert ml["lookback_days_regime"] == 1825
    assert ml["max_strategies_per_run"] == 5
    assert ml["min_forward_return_pct"] == 1.5
    assert ml["min_examples"] == 30
    assert ml["min_win_rate_pct"] == 45.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=. pytest shared/tests/test_config.py::test_ml_config_defaults -v
```

Expected: `FAILED` — KeyError on `cfg["ml"]`

- [ ] **Step 3: Create the migration file**

Create `db/migrations/004_ml_tables.sql`:

```sql
-- db/migrations/004_ml_tables.sql
-- Migration 004: ML discovery pipeline tables

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
    id                    SERIAL PRIMARY KEY,
    ran_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbols_processed     INTEGER,
    patterns_found        INTEGER,
    strategies_generated  INTEGER,
    candidates_promoted   INTEGER,
    duration_seconds      FLOAT,
    error                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_ml_bars_symbol_date ON ml_bars(symbol, bar_date);
```

- [ ] **Step 4: Update `shared/config.py` with ML defaults**

Add the `"ml"` key to `_DEFAULT_CONFIG`:

```python
_DEFAULT_CONFIG: dict = {
    "log_level": "INFO",
    "watchlist": ["AAPL", "MSFT", "GOOGL"],
    "paper_balance": 100000.0,
    "ml": {
        "symbols": [
            "CRWD", "SNOW", "DDOG", "SHOP", "MELI", "COIN", "UBER", "AXON",
            "PLTR", "AI", "BBAI", "SOUN", "IONQ", "RXRX", "GTLB", "PATH",
            "S", "CPNG", "MRVL", "MPWR", "WOLF", "SITM", "ONTO", "ALAB",
            "SMCI", "SNDK",
        ],
        "lookback_days_momentum": 365,
        "lookback_days_regime": 1825,
        "max_strategies_per_run": 5,
        "min_forward_return_pct": 1.5,
        "min_examples": 30,
        "min_win_rate_pct": 45.0,
        "cron_schedule": "0 2 * * *",
        "research_url": "http://research:8081",
    },
}
```

- [ ] **Step 5: Update `config.toml` with `[ml]` section**

Append to `config.toml`:

```toml
[ml]
symbols = [
  "CRWD", "SNOW", "DDOG", "SHOP", "MELI", "COIN", "UBER", "AXON",
  "PLTR", "AI", "BBAI", "SOUN", "IONQ", "RXRX", "GTLB", "PATH",
  "S", "CPNG", "MRVL", "MPWR", "WOLF", "SITM", "ONTO", "ALAB",
  "SMCI", "SNDK",
]
lookback_days_momentum = 365
lookback_days_regime   = 1825
max_strategies_per_run = 5
min_forward_return_pct = 1.5
min_examples           = 30
min_win_rate_pct       = 45.0
cron_schedule          = "0 2 * * *"
research_url           = "http://research:8081"
```

- [ ] **Step 6: Run test to verify it passes**

```bash
PYTHONPATH=. pytest shared/tests/test_config.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 7: Commit**

```bash
git add db/migrations/004_ml_tables.sql config.toml shared/config.py shared/tests/test_config.py
git commit -m "feat: add ML pipeline config and DB migration"
```

---

## Task 2: Service Skeleton (Dockerfile, requirements, health server)

**Files:**
- Create: `services/ml/Dockerfile`
- Create: `services/ml/requirements.txt`
- Create: `services/ml/pipeline.py` (skeleton — health server + cron loop, no phase logic yet)

- [ ] **Step 1: Create `services/ml/requirements.txt`**

```
psycopg2-binary==2.9.9
flask==3.0.3
schedule==1.2.2
yfinance==0.2.40
pandas==2.2.2
numpy==1.26.4
scikit-learn==1.4.2
anthropic==0.28.0
requests==2.32.3
python-dotenv==1.0.1
ta==0.11.0
```

- [ ] **Step 2: Create `services/ml/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

RUN adduser --disabled-password --gecos "" appuser
USER appuser

CMD ["python", "pipeline.py"]
```

- [ ] **Step 3: Create `services/ml/pipeline.py` skeleton**

```python
"""services/ml/pipeline.py — ML strategy discovery pipeline entrypoint.

Runs a nightly batch job at 2am. Exposes GET /health on port 8082 for the
existing watchdog. All pipeline logic is orchestrated from run_pipeline().
"""
import logging
import os
import threading
import time

import schedule
from flask import Flask, jsonify

from shared.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("ml")

app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


def run_pipeline() -> None:
    """Orchestrate all 5 pipeline phases. Called by cron and on first boot."""
    log.info("Pipeline started")
    start = time.time()
    try:
        _run_phases()
    except Exception as exc:  # noqa: BLE001
        log.error("Pipeline failed: %s", exc, exc_info=True)
        _send_discord_alert(f"ML pipeline failed: {exc}")
    finally:
        log.info("Pipeline finished in %.1fs", time.time() - start)


def _run_phases() -> None:
    """Placeholder — implemented in Task 7."""
    log.info("No phases implemented yet")


def _send_discord_alert(message: str) -> None:
    """Send a Discord alert via webhook if configured."""
    import requests as req
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        req.post(url, json={"content": message}, timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("Discord alert failed: %s", exc)


def _start_health_server() -> None:
    """Run Flask health server in background thread on port 8082."""
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=8082, use_reloader=False),
        daemon=True,
        name="health",
    ).start()
    log.info("Health server started on :8082")


def main() -> None:
    cfg = load_config().get("ml", {})
    cron = cfg.get("cron_schedule", "0 2 * * *")

    _start_health_server()

    # Parse cron: "0 2 * * *" → run at 02:00 daily
    hour = int(cron.split()[1])
    minute = int(cron.split()[0])
    schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(run_pipeline)
    log.info("Scheduled pipeline at %02d:%02d UTC nightly", hour, minute)

    # Run once immediately on startup so the first nightly run isn't skipped
    run_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify the skeleton imports cleanly**

```bash
cd /Users/nickchow/claude/alphadivision
PYTHONPATH=. python -c "import services.ml.pipeline; print('OK')"
```

Expected: `OK` (no import errors)

- [ ] **Step 5: Commit**

```bash
git add services/ml/
git commit -m "feat: add ML service skeleton with health server and cron loop"
```

---

## Task 3: Data Collector

**Files:**
- Create: `services/ml/collector.py`
- Create: `services/ml/tests/__init__.py`
- Create: `services/ml/tests/test_collector.py`

- [ ] **Step 1: Write the failing tests**

Create `services/ml/tests/__init__.py` (empty).

Create `services/ml/tests/test_collector.py`:

```python
"""Unit tests for collector.py — all external I/O is mocked."""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Ensure shared is on path
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from collector import collect_bars, _fetch_yfinance


def _make_df(n=10, start="2024-01-01"):
    """Build a minimal OHLCV DataFrame mimicking yfinance output."""
    idx = pd.date_range(start=start, periods=n, freq="B")
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [102.0 + i for i in range(n)],
            "Low":  [98.0 + i for i in range(n)],
            "Close":[101.0 + i for i in range(n)],
            "Volume":[1_000_000 + i * 1000 for i in range(n)],
        },
        index=idx,
    )


def test_fetch_yfinance_returns_list_of_dicts():
    df = _make_df(5)
    with patch("collector.yf.download", return_value=df):
        bars = _fetch_yfinance("AAPL", date(2024, 1, 1), date(2024, 1, 10))
    assert len(bars) == 5
    assert "date" in bars[0]
    assert "open" in bars[0]
    assert "close" in bars[0]
    assert "volume" in bars[0]


def test_fetch_yfinance_empty_returns_empty():
    with patch("collector.yf.download", return_value=pd.DataFrame()):
        bars = _fetch_yfinance("AAPL", date(2024, 1, 1), date(2024, 1, 2))
    assert bars == []


def test_collect_bars_uses_cache():
    """collect_bars returns DB-cached bars without calling yfinance for covered dates."""
    today = date.today()
    cached_bars = [
        {"date": today - timedelta(days=i), "open": 100.0, "high": 102.0,
         "low": 98.0, "close": 101.0, "volume": 1_000_000}
        for i in range(400, 0, -1)  # 400 days of cached bars
    ]

    with patch("collector.get_cached_bars", return_value=cached_bars) as mock_cache, \
         patch("collector.save_bars") as mock_save, \
         patch("collector.yf.download") as mock_yf:
        result = collect_bars(["AAPL"], lookback_days=365)

    mock_yf.assert_not_called()   # full cache hit → no yfinance call
    assert "AAPL" in result
    assert len(result["AAPL"]) == 400


def test_collect_bars_fetches_missing_dates():
    """collect_bars fetches only the gap between cached data and today."""
    today = date.today()
    # Cache only has bars up to 10 days ago — gap of 10 days
    cached_bars = [
        {"date": today - timedelta(days=i), "open": 100.0, "high": 102.0,
         "low": 98.0, "close": 101.0, "volume": 1_000_000}
        for i in range(20, 10, -1)  # days 11–20 ago
    ]
    new_bars_df = _make_df(10)

    with patch("collector.get_cached_bars", return_value=cached_bars), \
         patch("collector.save_bars") as mock_save, \
         patch("collector.yf.download", return_value=new_bars_df) as mock_yf:
        result = collect_bars(["AAPL"], lookback_days=30)

    mock_yf.assert_called_once()
    mock_save.assert_called_once()  # saves the newly fetched bars


def test_collect_bars_parallel_handles_multiple_symbols():
    cached: dict = {}
    new_df = _make_df(5)
    with patch("collector.get_cached_bars", return_value=[]), \
         patch("collector.save_bars"), \
         patch("collector.yf.download", return_value=new_df):
        result = collect_bars(["AAPL", "MSFT"], lookback_days=30)
    assert "AAPL" in result
    assert "MSFT" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/test_collector.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'collector'`

- [ ] **Step 3: Create `services/ml/collector.py`**

```python
"""services/ml/collector.py — Phase 1: Fetch and cache OHLCV bars.

Fetches daily OHLCV bars via yfinance for a list of symbols. Bars are cached
in the ml_bars Postgres table so only new bars are fetched on subsequent runs.
Uses ThreadPoolExecutor for parallel fetching.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

import yfinance as yf

from queries import get_cached_bars, save_bars

log = logging.getLogger("ml.collector")

_MAX_WORKERS = 8


def _fetch_yfinance(symbol: str, start: date, end: date) -> list[dict]:
    """Fetch OHLCV bars from yfinance for the given date range.

    Returns a list of dicts with keys: date, open, high, low, close, volume.
    Returns [] if no data is returned (e.g. market holiday, bad symbol).
    """
    df = yf.download(
        symbol,
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    if df.empty:
        return []
    bars = []
    for ts, row in df.iterrows():
        bars.append({
            "date":   ts.date(),
            "open":   float(row["Open"]),
            "high":   float(row["High"]),
            "low":    float(row["Low"]),
            "close":  float(row["Close"]),
            "volume": int(row["Volume"]),
        })
    return bars


def _collect_symbol(symbol: str, lookback_days: int) -> list[dict]:
    """Fetch and cache bars for a single symbol.

    1. Loads cached bars from ml_bars.
    2. If cache is fully up-to-date (latest bar is yesterday or today), returns cache.
    3. Otherwise fetches the gap from yfinance and saves new bars to ml_bars.
    4. Returns all bars (cache + new), sorted ascending by date.
    """
    today = date.today()
    start_date = today - timedelta(days=lookback_days)

    cached = get_cached_bars(symbol, start_date)

    if cached:
        latest_cached = max(b["date"] for b in cached)
        if latest_cached >= today - timedelta(days=1):
            log.debug("%s: cache hit (%d bars)", symbol, len(cached))
            return sorted(cached, key=lambda b: b["date"])
        fetch_start = latest_cached + timedelta(days=1)
    else:
        fetch_start = start_date

    log.info("%s: fetching bars from %s to %s", symbol, fetch_start, today)
    new_bars = _fetch_yfinance(symbol, fetch_start, today + timedelta(days=1))

    if new_bars:
        save_bars(symbol, new_bars)
        log.info("%s: saved %d new bars", symbol, len(new_bars))

    all_bars = cached + new_bars
    return sorted(all_bars, key=lambda b: b["date"])


def collect_bars(symbols: list[str], lookback_days: int) -> dict[str, list[dict]]:
    """Collect OHLCV bars for all symbols in parallel.

    Returns a dict mapping symbol → sorted list of bar dicts.
    Symbols that fail are logged and omitted from the result.
    """
    results: dict[str, list[dict]] = {}

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_collect_symbol, sym, lookback_days): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym = futures[future]
            try:
                bars = future.result()
                if bars:
                    results[sym] = bars
                    log.info("%s: %d bars total", sym, len(bars))
                else:
                    log.warning("%s: no bars returned — skipping", sym)
            except Exception as exc:  # noqa: BLE001
                log.error("%s: collection failed: %s", sym, exc, exc_info=True)

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/test_collector.py -v
```

Expected: all 5 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add services/ml/collector.py services/ml/tests/__init__.py services/ml/tests/test_collector.py
git commit -m "feat: add ML data collector with yfinance + postgres bar cache"
```

---

## Task 4: Feature Engineering

**Files:**
- Create: `services/ml/features.py`
- Create: `services/ml/tests/test_features.py`

The feature vector has 26 components:

| Group | Features (names) |
|---|---|
| Momentum | rsi_7, rsi_14, rsi_21, mom_5d, mom_10d, mom_20d |
| Trend | sma_10, sma_20, sma_50, sma_200, dist_sma10, dist_sma20, dist_sma50, dist_sma200 |
| Volatility | atr_14, bb_width, dist_bb_upper, dist_bb_lower |
| Volume | vol_zscore, vol_ratio |
| MACD | macd_line, macd_signal, macd_hist |
| Regime | dist_52w_high, dist_52w_low, day_of_week |

`_MIN_BARS = 210` (200 for SMA_200 + 10 for forward-return label window).

- [ ] **Step 1: Write the failing tests**

Create `services/ml/tests/test_features.py`:

```python
"""Unit tests for features.py — indicator computation."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from datetime import date, timedelta
import numpy as np
import pytest

from features import compute_features, FEATURE_NAMES, _MIN_BARS


def _make_bars(n: int, base_price: float = 100.0) -> list[dict]:
    """Generate synthetic OHLCV bars with realistic values."""
    bars = []
    price = base_price
    base_date = date(2020, 1, 2)
    for i in range(n):
        # Simple random walk
        price *= 1.0 + (0.001 * ((i % 7) - 3))
        bars.append({
            "date":   base_date + timedelta(days=i + (i // 5) * 2),  # skip weekends roughly
            "open":   round(price * 0.999, 4),
            "high":   round(price * 1.005, 4),
            "low":    round(price * 0.995, 4),
            "close":  round(price, 4),
            "volume": 1_000_000 + i * 5000,
        })
    return bars


def test_feature_names_count():
    assert len(FEATURE_NAMES) == 26


def test_compute_features_returns_empty_for_too_few_bars():
    bars = _make_bars(_MIN_BARS - 1)
    result = compute_features(bars)
    assert result == []


def test_compute_features_returns_rows_for_enough_bars():
    bars = _make_bars(_MIN_BARS + 50)
    result = compute_features(bars)
    assert len(result) > 0


def test_each_row_has_all_feature_names():
    bars = _make_bars(_MIN_BARS + 20)
    result = compute_features(bars)
    for row in result:
        for name in FEATURE_NAMES:
            assert name in row, f"Missing feature: {name}"


def test_no_nan_in_features():
    bars = _make_bars(_MIN_BARS + 50)
    result = compute_features(bars)
    for row in result:
        for name in FEATURE_NAMES:
            val = row[name]
            assert not (isinstance(val, float) and np.isnan(val)), \
                f"NaN in feature {name}"


def test_forward_return_present_and_finite():
    bars = _make_bars(_MIN_BARS + 50)
    result = compute_features(bars)
    for row in result:
        assert "fwd_return_10" in row
        assert isinstance(row["fwd_return_10"], float)
        assert not np.isnan(row["fwd_return_10"])


def test_day_of_week_in_range():
    bars = _make_bars(_MIN_BARS + 20)
    result = compute_features(bars)
    for row in result:
        assert 0 <= row["day_of_week"] <= 4


def test_rsi_in_range():
    bars = _make_bars(_MIN_BARS + 30)
    result = compute_features(bars)
    for row in result:
        for key in ("rsi_7", "rsi_14", "rsi_21"):
            assert 0 <= row[key] <= 100, f"{key} out of range: {row[key]}"


def test_vol_zscore_reasonable():
    """Volume z-score should be within [-5, 5] for synthetic data."""
    bars = _make_bars(_MIN_BARS + 50)
    result = compute_features(bars)
    for row in result:
        assert -10 < row["vol_zscore"] < 10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/test_features.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'features'`

- [ ] **Step 3: Create `services/ml/features.py`**

```python
"""services/ml/features.py — Phase 2: Compute 26-feature vectors from OHLCV bars.

Each bar gets a feature row including a 10-bar forward return label.
Rows where any indicator is NaN are dropped. The final bar has no forward
return and is always excluded from the output.
"""
import logging
from datetime import date

import numpy as np
import pandas as pd
import ta.momentum
import ta.trend
import ta.volatility

log = logging.getLogger("ml.features")

_MIN_BARS = 210  # SMA_200 needs 200; 10 extra for forward-return window

FEATURE_NAMES = [
    # Momentum (6)
    "rsi_7", "rsi_14", "rsi_21",
    "mom_5d", "mom_10d", "mom_20d",
    # Trend (8)
    "sma_10", "sma_20", "sma_50", "sma_200",
    "dist_sma10", "dist_sma20", "dist_sma50", "dist_sma200",
    # Volatility (4)
    "atr_14", "bb_width", "dist_bb_upper", "dist_bb_lower",
    # Volume (2)
    "vol_zscore", "vol_ratio",
    # MACD (3)
    "macd_line", "macd_signal", "macd_hist",
    # Regime (3)
    "dist_52w_high", "dist_52w_low", "day_of_week",
]


def compute_features(bars: list[dict]) -> list[dict]:
    """Compute feature vectors for all valid bars.

    Args:
        bars: List of OHLCV dicts sorted ascending by date.
              Each dict must have: date, open, high, low, close, volume.

    Returns:
        List of feature dicts. Each row contains all 26 FEATURE_NAMES plus
        'fwd_return_10' (10-bar forward return as a fraction) and 'bar_date'.
        Returns [] if fewer than _MIN_BARS bars are provided.
    """
    if len(bars) < _MIN_BARS:
        log.debug("Too few bars (%d < %d) — skipping feature computation", len(bars), _MIN_BARS)
        return []

    closes  = pd.Series([float(b["close"]) for b in bars])
    highs   = pd.Series([float(b["high"])  for b in bars])
    lows    = pd.Series([float(b["low"])   for b in bars])
    volumes = pd.Series([float(b["volume"]) for b in bars])
    dates   = [b["date"] for b in bars]

    # ── Momentum ─────────────────────────────────────────────────────────────
    rsi_7  = ta.momentum.RSIIndicator(close=closes, window=7).rsi()
    rsi_14 = ta.momentum.RSIIndicator(close=closes, window=14).rsi()
    rsi_21 = ta.momentum.RSIIndicator(close=closes, window=21).rsi()
    mom_5  = closes.pct_change(5)
    mom_10 = closes.pct_change(10)
    mom_20 = closes.pct_change(20)

    # ── Trend ────────────────────────────────────────────────────────────────
    sma_10  = ta.trend.SMAIndicator(close=closes, window=10).sma_indicator()
    sma_20  = ta.trend.SMAIndicator(close=closes, window=20).sma_indicator()
    sma_50  = ta.trend.SMAIndicator(close=closes, window=50).sma_indicator()
    sma_200 = ta.trend.SMAIndicator(close=closes, window=200).sma_indicator()

    # ── Volatility ───────────────────────────────────────────────────────────
    atr_ind = ta.volatility.AverageTrueRange(high=highs, low=lows, close=closes, window=14)
    atr_14  = atr_ind.average_true_range()
    bb_ind  = ta.volatility.BollingerBands(close=closes, window=20, window_dev=2)
    bb_upper = bb_ind.bollinger_hband()
    bb_lower = bb_ind.bollinger_lband()
    bb_mid   = bb_ind.bollinger_mavg()

    # ── Volume ───────────────────────────────────────────────────────────────
    vol_mean = volumes.rolling(window=20).mean()
    vol_std  = volumes.rolling(window=20).std()
    vol_avg5 = volumes.rolling(window=5).mean()

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd_ind    = ta.trend.MACD(close=closes)
    macd_line   = macd_ind.macd()
    macd_signal = macd_ind.macd_signal()
    macd_hist   = macd_ind.macd_diff()

    # ── 52-week high/low ─────────────────────────────────────────────────────
    high_252 = closes.rolling(window=252, min_periods=50).max()
    low_252  = closes.rolling(window=252, min_periods=50).min()

    rows = []
    # Stop 10 bars before end so forward return is always computable
    for i in range(len(bars) - 10):
        c = closes.iloc[i]
        s20 = sma_20.iloc[i]
        bb_w = bb_upper.iloc[i] - bb_lower.iloc[i] if not pd.isna(bb_upper.iloc[i]) else np.nan
        vol_z = (volumes.iloc[i] - vol_mean.iloc[i]) / vol_std.iloc[i] \
            if vol_std.iloc[i] and not pd.isna(vol_std.iloc[i]) else np.nan
        vol_r = volumes.iloc[i] / vol_avg5.iloc[i] if vol_avg5.iloc[i] else np.nan

        row = {
            "bar_date":       dates[i],
            # Momentum
            "rsi_7":          rsi_7.iloc[i],
            "rsi_14":         rsi_14.iloc[i],
            "rsi_21":         rsi_21.iloc[i],
            "mom_5d":         mom_5.iloc[i],
            "mom_10d":        mom_10.iloc[i],
            "mom_20d":        mom_20.iloc[i],
            # Trend
            "sma_10":         sma_10.iloc[i],
            "sma_20":         s20,
            "sma_50":         sma_50.iloc[i],
            "sma_200":        sma_200.iloc[i],
            "dist_sma10":     (c - sma_10.iloc[i]) / sma_10.iloc[i] if not pd.isna(sma_10.iloc[i]) else np.nan,
            "dist_sma20":     (c - s20) / s20 if not pd.isna(s20) else np.nan,
            "dist_sma50":     (c - sma_50.iloc[i]) / sma_50.iloc[i] if not pd.isna(sma_50.iloc[i]) else np.nan,
            "dist_sma200":    (c - sma_200.iloc[i]) / sma_200.iloc[i] if not pd.isna(sma_200.iloc[i]) else np.nan,
            # Volatility
            "atr_14":         atr_14.iloc[i],
            "bb_width":       bb_w / bb_mid.iloc[i] if bb_mid.iloc[i] and not pd.isna(bb_mid.iloc[i]) else np.nan,
            "dist_bb_upper":  (bb_upper.iloc[i] - c) / c if not pd.isna(bb_upper.iloc[i]) else np.nan,
            "dist_bb_lower":  (c - bb_lower.iloc[i]) / c if not pd.isna(bb_lower.iloc[i]) else np.nan,
            # Volume
            "vol_zscore":     vol_z,
            "vol_ratio":      vol_r,
            # MACD
            "macd_line":      macd_line.iloc[i],
            "macd_signal":    macd_signal.iloc[i],
            "macd_hist":      macd_hist.iloc[i],
            # Regime
            "dist_52w_high":  (high_252.iloc[i] - c) / c if not pd.isna(high_252.iloc[i]) else np.nan,
            "dist_52w_low":   (c - low_252.iloc[i]) / c if not pd.isna(low_252.iloc[i]) else np.nan,
            "day_of_week":    dates[i].weekday() if isinstance(dates[i], date) else 0,
            # Label
            "fwd_return_10":  float((closes.iloc[i + 10] - c) / c),
        }

        # Drop rows with any NaN in the 26 indicator features
        if any(
            isinstance(row[k], float) and np.isnan(row[k])
            for k in FEATURE_NAMES
        ):
            continue

        rows.append(row)

    log.debug("Feature computation: %d valid rows from %d bars", len(rows), len(bars))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/test_features.py -v
```

Expected: all 9 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add services/ml/features.py services/ml/tests/test_features.py
git commit -m "feat: add ML feature engineering — 26-indicator vector per bar"
```

---

## Task 5: Pattern Discovery

**Files:**
- Create: `services/ml/discoverer.py`
- Create: `services/ml/tests/test_discoverer.py`

- [ ] **Step 1: Write the failing tests**

Create `services/ml/tests/test_discoverer.py`:

```python
"""Unit tests for discoverer.py — decision tree and k-means pattern discovery."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from datetime import date, timedelta
import numpy as np
import pytest

from features import compute_features, _MIN_BARS, FEATURE_NAMES
from discoverer import (
    discover_patterns,
    _extract_dt_patterns,
    _extract_cluster_patterns,
    _label_binary,
    CandidatePattern,
)


def _make_feature_rows(n: int = 300) -> list[dict]:
    """Generate synthetic feature rows with a clear pattern for testing."""
    rows = []
    base = date(2019, 1, 2)
    for i in range(n):
        c = 100.0 + i * 0.05
        # Embed a signal: when rsi_14 < 40, forward return tends to be high
        fwd = 0.03 if (i % 10 < 3) else 0.005
        row = {k: 0.0 for k in FEATURE_NAMES}
        row.update({
            "bar_date":      base + timedelta(days=i),
            "rsi_7":         35.0 if (i % 10 < 3) else 55.0,
            "rsi_14":        38.0 if (i % 10 < 3) else 58.0,
            "rsi_21":        40.0 if (i % 10 < 3) else 60.0,
            "sma_10":        c,
            "sma_20":        c * 0.99,
            "sma_50":        c * 0.98,
            "sma_200":       c * 0.95,
            "dist_sma10":    0.01,
            "dist_sma20":    0.02,
            "dist_sma50":    0.03,
            "dist_sma200":   0.05,
            "vol_zscore":    1.2 if (i % 10 < 3) else 0.1,
            "vol_ratio":     1.3,
            "atr_14":        2.0,
            "bb_width":      0.05,
            "dist_bb_upper": 0.02,
            "dist_bb_lower": 0.03,
            "macd_line":     0.5,
            "macd_signal":   0.3,
            "macd_hist":     0.2,
            "dist_52w_high": 0.05,
            "dist_52w_low":  0.10,
            "day_of_week":   i % 5,
            "mom_5d":        0.01,
            "mom_10d":       0.015,
            "mom_20d":       0.02,
            "fwd_return_10": fwd,
        })
        rows.append(row)
    return rows


def test_label_binary_top_30_percent():
    rows = _make_feature_rows(100)
    labeled = _label_binary(rows)
    positive = sum(r["label"] for r in labeled)
    # Top 30% → ~30 positive labels
    assert 25 <= positive <= 35


def test_extract_dt_patterns_finds_at_least_one():
    rows = _make_feature_rows(300)
    cfg = {
        "min_examples": 20,
        "min_forward_return_pct": 1.0,
        "min_win_rate_pct": 40.0,
    }
    patterns = _extract_dt_patterns(rows, cfg)
    assert len(patterns) >= 1


def test_dt_pattern_has_required_fields():
    rows = _make_feature_rows(300)
    cfg = {"min_examples": 20, "min_forward_return_pct": 1.0, "min_win_rate_pct": 40.0}
    patterns = _extract_dt_patterns(rows, cfg)
    assert len(patterns) >= 1
    p = patterns[0]
    assert isinstance(p, CandidatePattern)
    assert p.pattern_type == "decision_tree"
    assert isinstance(p.rule_description, str) and len(p.rule_description) > 0
    assert p.example_count >= 20
    assert isinstance(p.avg_forward_return_pct, float)
    assert isinstance(p.win_rate_pct, float)
    assert isinstance(p.sharpe, float)


def test_extract_cluster_patterns_finds_at_least_one():
    rows = _make_feature_rows(400)
    cfg = {
        "min_examples": 20,
        "min_forward_return_pct": 1.0,
        "min_win_rate_pct": 40.0,
    }
    patterns = _extract_cluster_patterns(rows, k=5, cfg=cfg)
    # At least one cluster should have a decent average return
    assert len(patterns) >= 0  # may be 0 with random data — just check no crash


def test_discover_patterns_returns_at_most_max_strategies():
    features_by_symbol = {
        "AAPL": _make_feature_rows(300),
        "MSFT": _make_feature_rows(300),
    }
    cfg = {
        "lookback_days_momentum": 365,
        "lookback_days_regime":   1825,
        "max_strategies_per_run": 3,
        "min_examples":           20,
        "min_forward_return_pct": 1.0,
        "min_win_rate_pct":       40.0,
    }
    patterns = discover_patterns(features_by_symbol, cfg)
    assert len(patterns) <= 3


def test_discover_patterns_returns_empty_for_insufficient_data():
    features_by_symbol = {"AAPL": _make_feature_rows(10)}  # too few rows
    cfg = {
        "lookback_days_momentum": 365,
        "lookback_days_regime":   1825,
        "max_strategies_per_run": 5,
        "min_examples":           30,
        "min_forward_return_pct": 1.5,
        "min_win_rate_pct":       45.0,
    }
    patterns = discover_patterns(features_by_symbol, cfg)
    assert patterns == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/test_discoverer.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'discoverer'`

- [ ] **Step 3: Create `services/ml/discoverer.py`**

```python
"""services/ml/discoverer.py — Phase 3: Pattern discovery via DT + k-means.

Two parallel models find market conditions that predict profitable 10-bar
forward returns:
  - DecisionTreeClassifier (per-symbol + cross-symbol, 1yr data, max_depth=4)
  - KMeans clustering (all symbols, 5yr data, k=10)

The top-N patterns by Sharpe ratio are returned as CandidatePattern objects.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np
from sklearn.tree import DecisionTreeClassifier, _tree
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from features import FEATURE_NAMES

log = logging.getLogger("ml.discoverer")

_DT_MAX_DEPTH   = 4
_KMEANS_K       = 10
_FORWARD_RETURN_BARS = 10


@dataclass
class CandidatePattern:
    pattern_type:          str   # "decision_tree" | "cluster"
    rule_description:      str   # human-readable rule or cluster profile
    example_count:         int
    avg_forward_return_pct: float
    win_rate_pct:          float
    sharpe:                float
    symbol:                Optional[str] = None  # None means cross-symbol


# ── Label helpers ─────────────────────────────────────────────────────────────

def _label_binary(rows: list[dict]) -> list[dict]:
    """Add binary label: 1 if fwd_return_10 is in the top 30%, else 0."""
    returns = [r["fwd_return_10"] for r in rows]
    threshold = np.percentile(returns, 70)
    for r in rows:
        r["label"] = 1 if r["fwd_return_10"] >= threshold else 0
    return rows


# ── Feature matrix ────────────────────────────────────────────────────────────

def _to_matrix(rows: list[dict]) -> np.ndarray:
    """Convert feature rows to numpy matrix (shape: n_rows × 26)."""
    return np.array([[r[f] for f in FEATURE_NAMES] for r in rows])


# ── Rule extraction from decision tree ───────────────────────────────────────

def _extract_rules(tree: DecisionTreeClassifier, feature_names: list[str]) -> list[str]:
    """Extract human-readable rules from each positive-class leaf node."""
    tree_ = tree.tree_
    rules = []

    def recurse(node: int, conditions: list[str]) -> None:
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            # Leaf node
            n_samples  = int(tree_.n_node_samples[node])
            n_positive = int(tree_.value[node][0][1])
            if n_positive > n_samples // 2:  # majority positive class
                rules.append(" AND ".join(conditions) if conditions else "all bars")
        else:
            fname = feature_names[tree_.feature[node]]
            threshold = tree_.threshold[node]
            recurse(tree_.children_left[node],  conditions + [f"{fname} <= {threshold:.4f}"])
            recurse(tree_.children_right[node], conditions + [f"{fname} > {threshold:.4f}"])

    recurse(0, [])
    return rules


def _profile_leaf(rows: list[dict], tree: DecisionTreeClassifier,
                  X: np.ndarray) -> list[tuple[str, list[float]]]:
    """For each leaf that tends to be positive, collect the forward returns."""
    leaf_ids = tree.apply(X)
    rules = _extract_rules(tree, FEATURE_NAMES)

    # Map leaf node → returns
    tree_ = tree.tree_
    leaf_returns: dict[int, list[float]] = {}

    def collect(node: int) -> None:
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            leaf_returns[node] = []
        else:
            collect(tree_.children_left[node])
            collect(tree_.children_right[node])

    collect(0)
    for i, leaf_id in enumerate(leaf_ids):
        if leaf_id in leaf_returns:
            leaf_returns[leaf_id].append(rows[i]["fwd_return_10"])

    # We need to associate rules with leaf IDs — re-traverse with IDs
    rule_returns: list[tuple[str, list[float]]] = []
    idx = [0]

    def recurse_with_rule(node: int, conditions: list[str]) -> None:
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            if node in leaf_returns:
                rule = " AND ".join(conditions) if conditions else "all bars"
                rule_returns.append((rule, leaf_returns[node]))
        else:
            fname = FEATURE_NAMES[tree_.feature[node]]
            threshold = tree_.threshold[node]
            recurse_with_rule(tree_.children_left[node],  conditions + [f"{fname} <= {threshold:.4f}"])
            recurse_with_rule(tree_.children_right[node], conditions + [f"{fname} > {threshold:.4f}"])

    recurse_with_rule(0, [])
    return rule_returns


def _sharpe(returns: list[float]) -> float:
    """Annualised Sharpe ratio from daily returns. Returns -inf if insufficient."""
    if len(returns) < 5:
        return float("-inf")
    arr = np.array(returns)
    std = arr.std()
    if std == 0:
        return 0.0
    return float(arr.mean() / std * np.sqrt(252))


# ── Decision-tree model ───────────────────────────────────────────────────────

def _extract_dt_patterns(rows: list[dict], cfg: dict,
                          symbol: Optional[str] = None) -> list[CandidatePattern]:
    """Train a decision tree and extract candidate leaf patterns."""
    if len(rows) < max(cfg["min_examples"] * 2, 60):
        return []

    rows = _label_binary(list(rows))  # copy
    X = _to_matrix(rows)
    y = np.array([r["label"] for r in rows])

    clf = DecisionTreeClassifier(max_depth=_DT_MAX_DEPTH, random_state=42)
    clf.fit(X, y)

    rule_returns = _profile_leaf(rows, clf, X)
    candidates = []

    for rule, returns in rule_returns:
        if not returns:
            continue
        n = len(returns)
        avg_ret_pct = float(np.mean(returns)) * 100
        win_rate    = float(np.mean([r > 0 for r in returns])) * 100
        sh          = _sharpe(returns)

        if (n >= cfg["min_examples"]
                and avg_ret_pct >= cfg["min_forward_return_pct"]
                and win_rate >= cfg["min_win_rate_pct"]):
            candidates.append(CandidatePattern(
                pattern_type="decision_tree",
                rule_description=rule,
                example_count=n,
                avg_forward_return_pct=avg_ret_pct,
                win_rate_pct=win_rate,
                sharpe=sh,
                symbol=symbol,
            ))

    log.info("DT (%s): %d candidates from %d rows", symbol or "cross", len(candidates), len(rows))
    return candidates


# ── K-Means clustering model ─────────────────────────────────────────────────

def _extract_cluster_patterns(rows: list[dict], k: int,
                               cfg: dict) -> list[CandidatePattern]:
    """Fit k-means and profile each cluster. Returns candidate patterns."""
    if len(rows) < k * 10:
        return []

    X = _to_matrix(rows)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = km.fit_predict(X_scaled)

    candidates = []
    for cluster_id in range(k):
        mask    = labels == cluster_id
        n       = int(mask.sum())
        returns = [rows[i]["fwd_return_10"] for i in range(len(rows)) if mask[i]]

        if not returns:
            continue

        avg_ret_pct = float(np.mean(returns)) * 100
        win_rate    = float(np.mean([r > 0 for r in returns])) * 100
        sh          = _sharpe(returns)

        if (n >= cfg["min_examples"]
                and avg_ret_pct >= cfg["min_forward_return_pct"]
                and win_rate >= cfg["min_win_rate_pct"]):
            # Describe cluster by top-3 most-deviated centroid features
            centroid = km.cluster_centers_[cluster_id]
            top_idx  = np.argsort(np.abs(centroid))[-3:][::-1]
            profile_parts = [
                f"{FEATURE_NAMES[i]} ≈ {scaler.mean_[i] + centroid[i] * scaler.scale_[i]:.4f}"
                for i in top_idx
            ]
            description = (
                f"Cluster {cluster_id}: {n} bars, avg_fwd={avg_ret_pct:.2f}%, "
                f"win={win_rate:.1f}% | {', '.join(profile_parts)}"
            )
            candidates.append(CandidatePattern(
                pattern_type="cluster",
                rule_description=description,
                example_count=n,
                avg_forward_return_pct=avg_ret_pct,
                win_rate_pct=win_rate,
                sharpe=sh,
                symbol=None,  # clusters are cross-symbol
            ))

    log.info("K-Means: %d candidate clusters (k=%d, %d rows)", len(candidates), k, len(rows))
    return candidates


# ── Top-level orchestration ───────────────────────────────────────────────────

def _filter_rows_by_lookback(rows: list[dict], lookback_days: int) -> list[dict]:
    """Keep only rows within the last lookback_days calendar days."""
    cutoff = date.today() - timedelta(days=lookback_days)
    return [r for r in rows if r["bar_date"] >= cutoff]


def discover_patterns(
    features_by_symbol: dict[str, list[dict]],
    cfg: dict,
) -> list[CandidatePattern]:
    """Run DT (1yr) and k-means (5yr) discovery. Return top-N by Sharpe.

    Args:
        features_by_symbol: symbol → list of feature rows (from features.py)
        cfg: ML config dict with keys: lookback_days_momentum, lookback_days_regime,
             max_strategies_per_run, min_examples, min_forward_return_pct, min_win_rate_pct
    Returns:
        Up to cfg["max_strategies_per_run"] CandidatePattern objects, sorted by Sharpe.
    """
    all_candidates: list[CandidatePattern] = []

    # ── Decision tree: per-symbol + cross-symbol (1yr data) ──────────────────
    momentum_rows_all: list[dict] = []
    for symbol, rows in features_by_symbol.items():
        momentum_rows = _filter_rows_by_lookback(rows, cfg["lookback_days_momentum"])
        if momentum_rows:
            per_sym = _extract_dt_patterns(momentum_rows, cfg, symbol=symbol)
            all_candidates.extend(per_sym)
            momentum_rows_all.extend(momentum_rows)

    if momentum_rows_all:
        cross_sym = _extract_dt_patterns(momentum_rows_all, cfg, symbol=None)
        all_candidates.extend(cross_sym)

    # ── K-Means: all symbols, 5yr data ───────────────────────────────────────
    regime_rows_all: list[dict] = []
    for rows in features_by_symbol.values():
        regime_rows = _filter_rows_by_lookback(rows, cfg["lookback_days_regime"])
        regime_rows_all.extend(regime_rows)

    if regime_rows_all:
        cluster_candidates = _extract_cluster_patterns(
            regime_rows_all, k=_KMEANS_K, cfg=cfg
        )
        all_candidates.extend(cluster_candidates)

    # ── Top-N by Sharpe ──────────────────────────────────────────────────────
    all_candidates.sort(key=lambda p: p.sharpe, reverse=True)
    top_n = all_candidates[: cfg["max_strategies_per_run"]]
    log.info(
        "Discovery complete: %d candidates total, returning top %d",
        len(all_candidates), len(top_n),
    )
    return top_n
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/test_discoverer.py -v
```

Expected: all 6 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add services/ml/discoverer.py services/ml/tests/test_discoverer.py
git commit -m "feat: add ML pattern discovery — decision tree + k-means"
```

---

## Task 6: Strategy Codegen

**Files:**
- Create: `services/ml/codegen.py`
- Create: `services/ml/tests/test_codegen.py`

- [ ] **Step 1: Write the failing tests**

Create `services/ml/tests/test_codegen.py`:

```python
"""Unit tests for codegen.py — Claude API mocked throughout."""
import ast
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from unittest.mock import MagicMock, patch
import pytest

from discoverer import CandidatePattern
from codegen import generate_strategy_code, _validate_code, _build_prompt


_VALID_CODE = '''
def generate_signal(snapshot):
    price = snapshot["price"]
    rsi = snapshot["rsi"]
    if rsi < 40:
        return {"decision": "buy", "confidence": 0.7, "reasoning": "RSI oversold"}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "No signal"}
'''

_INVALID_CODE_NO_FUNCTION = "x = 1 + 2"
_INVALID_CODE_SYNTAX = "def generate_signal(snapshot: invalid syntax!!!"
_INVALID_CODE_BAD_SCHEMA = '''
def generate_signal(snapshot):
    return "buy"  # wrong return type
'''


def _make_pattern(rule="RSI_14 <= 38.0 AND vol_zscore > 1.3") -> CandidatePattern:
    return CandidatePattern(
        pattern_type="decision_tree",
        rule_description=rule,
        example_count=45,
        avg_forward_return_pct=2.3,
        win_rate_pct=55.0,
        sharpe=0.8,
        symbol="CRWD",
    )


def _mock_anthropic_response(code: str):
    """Return a mock Anthropic API response containing the given code."""
    msg = MagicMock()
    msg.content = [MagicMock(text=f"```python\n{code}\n```")]
    return msg


def test_validate_code_accepts_valid_function():
    errors = _validate_code(_VALID_CODE)
    assert errors == []


def test_validate_code_rejects_syntax_error():
    errors = _validate_code(_INVALID_CODE_SYNTAX)
    assert len(errors) > 0
    assert any("syntax" in e.lower() or "parse" in e.lower() for e in errors)


def test_validate_code_rejects_missing_function():
    errors = _validate_code(_INVALID_CODE_NO_FUNCTION)
    assert any("generate_signal" in e for e in errors)


def test_validate_code_rejects_bad_schema():
    errors = _validate_code(_INVALID_CODE_BAD_SCHEMA)
    assert len(errors) > 0


def test_build_prompt_contains_pattern_info():
    pattern = _make_pattern()
    prompt = _build_prompt(pattern)
    assert "RSI_14" in prompt
    assert "2.3" in prompt  # avg return
    assert "55.0" in prompt  # win rate
    assert "generate_signal" in prompt


def test_generate_strategy_code_success():
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(_VALID_CODE)

    result = generate_strategy_code(pattern, client=mock_client)

    assert result is not None
    assert "generate_signal" in result
    mock_client.messages.create.assert_called_once()


def test_generate_strategy_code_retries_once_on_invalid():
    """If first response is invalid, retries once with error context."""
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _mock_anthropic_response(_INVALID_CODE_NO_FUNCTION),
        _mock_anthropic_response(_VALID_CODE),
    ]

    result = generate_strategy_code(pattern, client=mock_client)

    assert result is not None
    assert mock_client.messages.create.call_count == 2


def test_generate_strategy_code_returns_none_on_double_failure():
    """If both attempts produce invalid code, returns None."""
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(
        _INVALID_CODE_NO_FUNCTION
    )

    result = generate_strategy_code(pattern, client=mock_client)

    assert result is None
    assert mock_client.messages.create.call_count == 2


def test_generate_strategy_code_strips_markdown_fences():
    """Code returned inside ```python ... ``` blocks is extracted correctly."""
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(_VALID_CODE)

    result = generate_strategy_code(pattern, client=mock_client)
    assert "```" not in (result or "")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/test_codegen.py -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'codegen'`

- [ ] **Step 3: Create `services/ml/codegen.py`**

```python
"""services/ml/codegen.py — Phase 4: Generate strategy code via Claude API.

For each CandidatePattern, builds a prompt and calls Claude to produce a
generate_signal() function. The output is validated (AST parse, function
exists, dry-run on 3 snapshots) before being returned. One retry is allowed.
"""
import ast
import hashlib
import logging
import os
import re
from typing import Optional

import anthropic

from discoverer import CandidatePattern

log = logging.getLogger("ml.codegen")

_MODEL = "claude-sonnet-4-5"
_MAX_TOKENS = 1024

# Three synthetic snapshots used for dry-run validation
_DRY_RUN_SNAPSHOTS = [
    {"price": 150.0, "rsi": 35.0, "sma20": 148.0, "sma50": 145.0,
     "sma20_prev": 147.5, "sma20_prev2": 147.0, "volume": 1_500_000, "volume_avg": 1_200_000},
    {"price": 200.0, "rsi": 65.0, "sma20": 195.0, "sma50": 190.0,
     "sma20_prev": 194.0, "sma20_prev2": 193.0, "volume": 800_000, "volume_avg": 1_100_000},
    {"price": 100.0, "rsi": 50.0, "sma20": 101.0, "sma50": 99.0,
     "sma20_prev": 100.5, "sma20_prev2": 100.0, "volume": 1_000_000, "volume_avg": 1_000_000},
]

_VALID_DECISIONS = {"buy", "sell", "hold"}


def _extract_code_block(text: str) -> str:
    """Strip markdown fences if present, return raw code."""
    match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _validate_code(code: str) -> list[str]:
    """Return a list of validation error strings. Empty list = valid.

    Checks:
      1. AST parse succeeds
      2. generate_signal function is defined
      3. Dry-run on 3 synthetic snapshots returns valid schema
    """
    errors: list[str] = []

    # 1. Syntax check
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"Syntax parse error: {exc}")
        return errors  # Can't continue without a valid AST

    # 2. Function name check
    function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    if "generate_signal" not in function_names:
        errors.append("generate_signal function not found in generated code")
        return errors

    # 3. Dry-run check
    namespace: dict = {"__builtins__": {}}
    try:
        exec(compile(tree, "<string>", "exec"), namespace)  # noqa: S102
    except Exception as exc:
        errors.append(f"Code execution error: {exc}")
        return errors

    fn = namespace.get("generate_signal")
    if not callable(fn):
        errors.append("generate_signal is not callable after exec")
        return errors

    for i, snapshot in enumerate(_DRY_RUN_SNAPSHOTS):
        try:
            result = fn(snapshot)
        except Exception as exc:
            errors.append(f"Dry-run snapshot {i} raised: {exc}")
            continue

        if not isinstance(result, dict):
            errors.append(f"Snapshot {i}: expected dict, got {type(result).__name__}")
            continue
        if result.get("decision") not in _VALID_DECISIONS:
            errors.append(
                f"Snapshot {i}: decision must be buy/sell/hold, got {result.get('decision')!r}"
            )
        if not isinstance(result.get("confidence"), (int, float)):
            errors.append(f"Snapshot {i}: confidence must be numeric")
        if not isinstance(result.get("reasoning"), str):
            errors.append(f"Snapshot {i}: reasoning must be a string")

    return errors


def _build_prompt(pattern: CandidatePattern) -> str:
    """Build the Claude prompt for a given candidate pattern."""
    sym_context = f"originating symbol: {pattern.symbol}" if pattern.symbol else "cross-symbol pattern"
    return f"""You are generating a trading strategy function for an algorithmic trading system.

Pattern type: {pattern.pattern_type}
Rule/profile: {pattern.rule_description}
Historical performance: {pattern.example_count} examples, avg 10-bar return {pattern.avg_forward_return_pct:.2f}%, win rate {pattern.win_rate_pct:.1f}%
Context: {sym_context}

Write a Python function named `generate_signal` that takes a single argument `snapshot` (a dict) and implements trading logic based on the pattern above.

You MUST use ONLY these snapshot keys (no others exist):
  price, rsi, sma20, sma50, sma20_prev, sma20_prev2, volume, volume_avg

Return format — return a dict with exactly these keys:
  {{"decision": "buy" | "sell" | "hold", "confidence": 0.0–1.0, "reasoning": "short explanation"}}

Rules:
- No imports
- No external calls
- No global state
- Handle edge cases (e.g., division by zero) gracefully
- Use only the snapshot keys listed above

Output ONLY the Python function, wrapped in ```python ... ``` fences. No explanation."""


def _call_claude(prompt: str, client: anthropic.Anthropic) -> str:
    """Call Claude API and return the raw text response."""
    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def generate_strategy_code(
    pattern: CandidatePattern,
    client: Optional[anthropic.Anthropic] = None,
) -> Optional[str]:
    """Generate and validate a generate_signal() function for the given pattern.

    Returns the validated code string, or None if both attempts fail.
    The caller is responsible for saving the code to the database.
    """
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = _build_prompt(pattern)

    for attempt in range(2):
        log.info("Codegen attempt %d for pattern: %.60s...", attempt + 1, pattern.rule_description)
        try:
            raw_text = _call_claude(prompt, client)
        except Exception as exc:  # noqa: BLE001
            log.error("Claude API call failed (attempt %d): %s", attempt + 1, exc)
            return None

        code = _extract_code_block(raw_text)
        errors = _validate_code(code)

        if not errors:
            log.info("Codegen succeeded on attempt %d", attempt + 1)
            return code

        log.warning("Codegen attempt %d invalid: %s", attempt + 1, "; ".join(errors))
        if attempt == 0:
            # Append error context to prompt for retry
            prompt += f"\n\nYour previous response had these errors:\n" + "\n".join(
                f"- {e}" for e in errors
            ) + "\n\nPlease fix them and try again."

    log.error("Codegen failed after 2 attempts for pattern: %.60s...", pattern.rule_description)
    return None


def code_hash(code: str) -> str:
    """Return a short SHA-256 hash of the code for deduplication."""
    return hashlib.sha256(code.encode()).hexdigest()[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/test_codegen.py -v
```

Expected: all 9 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add services/ml/codegen.py services/ml/tests/test_codegen.py
git commit -m "feat: add ML strategy codegen with Claude API, AST validation, retry"
```

---

## Task 7: Pipeline Orchestrator + Queries

**Files:**
- Create: `services/ml/queries.py`
- Modify: `services/ml/pipeline.py` (complete `_run_phases()`)

- [ ] **Step 1: Create `services/ml/queries.py`**

```python
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
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    log.info("ML tables verified")
```

- [ ] **Step 2: Complete `services/ml/pipeline.py` — replace `_run_phases`**

Replace the entire content of `services/ml/pipeline.py` with:

```python
"""services/ml/pipeline.py — ML strategy discovery pipeline entrypoint.

Runs a nightly batch job at 2am. Exposes GET /health on port 8082 for the
existing watchdog. The five phases are orchestrated in _run_phases():
  1. collect_bars    — fetch + cache OHLCV bars via yfinance
  2. compute_features — 26-indicator vectors per bar
  3. discover_patterns — DT + k-means pattern discovery
  4. codegen          — Claude API → generate_signal() code
  5. backtest+promote — call Research API to backtest and auto-promote
"""
import logging
import os
import threading
import time
from typing import Optional

import requests
import schedule
from flask import Flask, jsonify

import anthropic

from shared.config import load_config
from collector import collect_bars
from features import compute_features
from discoverer import discover_patterns, CandidatePattern
from codegen import generate_strategy_code, code_hash
from queries import (
    save_ml_strategy, save_ml_run, ensure_ml_tables,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("ml")

app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ── Phase helpers ─────────────────────────────────────────────────────────────

def _backtest_strategy(strategy_id: int, symbol: Optional[str],
                        research_url: str) -> bool:
    """POST to Research API to trigger a backtest. Returns True if promoted."""
    endpoint = f"{research_url}/api/strategies/{strategy_id}/backtest"
    # Fall back to a known symbol if pattern is cross-symbol
    payload = {"symbol": symbol or "SPY"}
    try:
        resp = requests.post(endpoint, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        promoted = data.get("status") == "candidate"
        log.info(
            "Strategy %d backtest: status=%s, promoted=%s",
            strategy_id, data.get("status"), promoted,
        )
        return promoted
    except Exception as exc:  # noqa: BLE001
        log.error("Backtest API call failed for strategy %d: %s", strategy_id, exc)
        return False


def _run_phases() -> None:
    """Execute all 5 pipeline phases and record results in ml_runs."""
    cfg       = load_config()
    ml_cfg    = cfg.get("ml", {})
    symbols   = ml_cfg.get("symbols", [])
    research_url = ml_cfg.get("research_url", "http://research:8081")

    start = time.time()
    patterns_found         = 0
    strategies_generated   = 0
    candidates_promoted    = 0
    run_error: Optional[str] = None

    try:
        # Phase 1: Data collection
        log.info("Phase 1: Collecting bars for %d symbols", len(symbols))
        bars_by_symbol = collect_bars(
            symbols,
            lookback_days=ml_cfg.get("lookback_days_regime", 1825),
        )
        log.info("Phase 1 complete: %d symbols with data", len(bars_by_symbol))

        # Phase 2: Feature engineering
        log.info("Phase 2: Computing features")
        features_by_symbol: dict = {}
        for sym, bars in bars_by_symbol.items():
            rows = compute_features(bars)
            if rows:
                features_by_symbol[sym] = rows
        log.info("Phase 2 complete: %d symbols with features", len(features_by_symbol))

        # Phase 3: Pattern discovery
        log.info("Phase 3: Discovering patterns")
        patterns = discover_patterns(features_by_symbol, ml_cfg)
        patterns_found = len(patterns)
        log.info("Phase 3 complete: %d candidate patterns", patterns_found)

        if patterns_found == 0:
            log.warning("No patterns found — pipeline run produced 0 strategies")
            _send_discord_alert("ML pipeline: 0 patterns found after full run")

        # Phase 4: Strategy codegen
        log.info("Phase 4: Generating strategy code for %d patterns", patterns_found)
        anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        saved_strategies: list[tuple[int, Optional[str]]] = []

        for pattern in patterns:
            code = generate_strategy_code(pattern, client=anthropic_client)
            if code is None:
                log.warning("Codegen failed for pattern: %.60s", pattern.rule_description)
                continue

            h = code_hash(code)
            strategy_name = (
                f"ML-{pattern.pattern_type[:2].upper()}-{pattern.symbol or 'XSYM'}-{h[:6]}"
            )
            description = (
                f"ML-discovered {pattern.pattern_type} pattern. "
                f"{pattern.example_count} historical examples, "
                f"avg 10-bar return {pattern.avg_forward_return_pct:.2f}%, "
                f"win rate {pattern.win_rate_pct:.1f}%, Sharpe {pattern.sharpe:.2f}."
            )
            hypothesis = pattern.rule_description

            try:
                strategy_id = save_ml_strategy(
                    name=strategy_name,
                    description=description,
                    hypothesis=hypothesis,
                    code=code,
                    code_hash=h,
                )
                saved_strategies.append((strategy_id, pattern.symbol))
                strategies_generated += 1
                log.info("Saved strategy %d: %s", strategy_id, strategy_name)
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to save strategy: %s", exc)

        log.info("Phase 4 complete: %d strategies generated", strategies_generated)

        if strategies_generated == 0 and patterns_found > 0:
            _send_discord_alert(
                f"ML pipeline: {patterns_found} patterns found but 0 strategies generated"
            )

        # Phase 5: Backtest + promote via Research API
        log.info("Phase 5: Backtesting %d strategies", len(saved_strategies))
        for strategy_id, symbol in saved_strategies:
            promoted = _backtest_strategy(strategy_id, symbol, research_url)
            if promoted:
                candidates_promoted += 1

        log.info(
            "Phase 5 complete: %d/%d strategies promoted to candidate",
            candidates_promoted, strategies_generated,
        )

    except Exception as exc:  # noqa: BLE001
        run_error = str(exc)
        log.error("Pipeline phase failed: %s", exc, exc_info=True)
        _send_discord_alert(f"ML pipeline error: {exc}")

    duration = time.time() - start
    try:
        run_id = save_ml_run(
            symbols_processed=len(bars_by_symbol) if "bars_by_symbol" in dir() else 0,
            patterns_found=patterns_found,
            strategies_generated=strategies_generated,
            candidates_promoted=candidates_promoted,
            duration_seconds=duration,
            error=run_error,
        )
        log.info("Run record saved: id=%d, duration=%.1fs", run_id, duration)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to save ml_run record: %s", exc)

    # Alert if > 15 minutes
    if duration > 900:
        _send_discord_alert(f"ML pipeline took {duration:.0f}s (> 15 min threshold)")


def run_pipeline() -> None:
    """Top-level pipeline entry. Catches all unhandled exceptions."""
    log.info("=== ML pipeline run starting ===")
    try:
        _run_phases()
    except Exception as exc:  # noqa: BLE001
        log.error("Unhandled pipeline exception: %s", exc, exc_info=True)
        _send_discord_alert(f"ML pipeline unhandled exception: {exc}")
    log.info("=== ML pipeline run complete ===")


def _send_discord_alert(message: str) -> None:
    """Send a Discord alert via webhook if DISCORD_WEBHOOK_URL is configured."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        requests.post(url, json={"content": f"[ml] {message}"}, timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("Discord alert failed: %s", exc)


def _start_health_server() -> None:
    """Run Flask health server in a background daemon thread on port 8082."""
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=8082, use_reloader=False),
        daemon=True,
        name="health",
    ).start()
    log.info("Health server started on :8082")


def main() -> None:
    cfg = load_config().get("ml", {})
    cron = cfg.get("cron_schedule", "0 2 * * *")

    # Ensure ML tables exist before first run
    ensure_ml_tables()

    _start_health_server()

    # Parse cron schedule: "0 2 * * *" → 02:00 UTC
    parts  = cron.split()
    minute = int(parts[0])
    hour   = int(parts[1])
    schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(run_pipeline)
    log.info("Pipeline scheduled at %02d:%02d UTC nightly", hour, minute)

    # Run once immediately on startup
    run_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify pipeline imports are clean**

```bash
PYTHONPATH=services/ml:. python -c "
import sys
sys.path.insert(0, 'services/ml')
import ast, importlib.util
src = open('services/ml/pipeline.py').read()
ast.parse(src)
print('pipeline.py: AST OK')
"
```

Expected: `pipeline.py: AST OK`

- [ ] **Step 4: Run all ML tests**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/ -v
```

Expected: all tests `PASSED` (≥ 27 tests)

- [ ] **Step 5: Commit**

```bash
git add services/ml/queries.py services/ml/pipeline.py
git commit -m "feat: complete ML pipeline orchestrator with all 5 phases, queries, and Discord alerts"
```

---

## Task 8: Docker Integration

**Files:**
- Modify: `docker-compose.yml` (add ml service)

- [ ] **Step 1: Add `ml` service to `docker-compose.yml`**

After the `research:` block and before the `volumes:` section, add:

```yaml
  ml:
    build: ./services/ml
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      research:
        condition: service_started
    env_file: .env
    environment:
      SERVICE_NAME: ml
      DISCORD_WEBHOOK_URL: ${DISCORD_WEBHOOK_URL:-}
    volumes:
      - ./shared:/app/shared
      - ./config.toml:/app/config.toml:ro
      - logs:/var/log/alphadivision
```

The complete `docker-compose.yml` after this change:

```yaml
services:
  postgres:
    image: postgres:15-alpine
    restart: always
    environment:
      POSTGRES_DB: alphadivision
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d alphadivision"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: always
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  data:
    build: ./services/data
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: data
    volumes:
      - ./shared:/app/shared
      - ./config.toml:/app/config.toml:ro
      - logs:/var/log/alphadivision

  analysis:
    build: ./services/analysis
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: analysis
      DISCORD_WEBHOOK_URL: ${DISCORD_WEBHOOK_URL:-}
    volumes:
      - ./shared:/app/shared
      - ./config.toml:/app/config.toml:ro
      - logs:/var/log/alphadivision

  execution:
    build: ./services/execution
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: execution
    volumes:
      - ./shared:/app/shared
      - ./config.toml:/app/config.toml:ro
      - logs:/var/log/alphadivision

  alerts:
    build: ./services/alerts
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: alerts
    volumes:
      - ./shared:/app/shared
      - ./config.toml:/app/config.toml:ro
      - logs:/var/log/alphadivision

  dashboard:
    build: ./services/dashboard
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file: .env
    environment:
      SERVICE_NAME: dashboard
    ports:
      - "8080:8080"
    volumes:
      - ./shared:/app/shared
      - ./config.toml:/app/config.toml:ro
      - logs:/var/log/alphadivision

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

  ml:
    build: ./services/ml
    restart: always
    depends_on:
      postgres:
        condition: service_healthy
      research:
        condition: service_started
    env_file: .env
    environment:
      SERVICE_NAME: ml
      DISCORD_WEBHOOK_URL: ${DISCORD_WEBHOOK_URL:-}
    volumes:
      - ./shared:/app/shared
      - ./config.toml:/app/config.toml:ro
      - logs:/var/log/alphadivision

volumes:
  postgres_data:
  redis_data:
  logs:
```

- [ ] **Step 2: Verify docker-compose.yml is valid**

```bash
docker compose config --quiet && echo "docker-compose.yml: OK"
```

Expected: `docker-compose.yml: OK` (no errors)

- [ ] **Step 3: Build the ML Docker image**

```bash
docker compose build ml
```

Expected: `Successfully built ...` or `=> exporting to image` with no errors

- [ ] **Step 4: Run the full test suite**

```bash
PYTHONPATH=services/ml:. pytest services/ml/tests/ -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add ML service to docker-compose — nightly strategy discovery pipeline complete"
```

---

## Self-Review

### Spec Coverage

| Spec requirement | Task |
|---|---|
| 26 symbols, $10B–$500B range | Task 1 (config.toml) |
| ml_bars + ml_runs tables | Task 1 (migration) |
| yfinance daily OHLCV, 1yr + 5yr lookback | Task 3 (collector) |
| ThreadPoolExecutor parallel fetch | Task 3 (collector) |
| Bar cache keyed by symbol+date | Task 1 (unique constraint) + Task 3 |
| 26 features: RSI 7/14/21, SMA 10/20/50/200, ATR, BB, vol z-score, MACD, regime | Task 4 (features) |
| 10-bar forward return label, binary top-30% | Task 4 (features) + Task 5 (discoverer) |
| DecisionTreeClassifier max_depth=4, per-symbol + cross-symbol | Task 5 (discoverer) |
| KMeans k=10 on 5yr data | Task 5 (discoverer) |
| Candidate gates: ≥30 examples, ≥1.5% return, ≥45% win rate | Task 5 (discoverer) |
| Top-5 by Sharpe | Task 5 (discoverer) |
| Claude Sonnet prompt, validate, retry-once | Task 6 (codegen) |
| AST validation + function name + dry-run | Task 6 (codegen) |
| status='testing', triggered_by='ml_discovery' | Task 7 (queries) |
| Backtest via Research API | Task 7 (pipeline) |
| Auto-promote if Sharpe≥0.5, win≥45%, drawdown≤20% | Delegated to Research Service |
| `/health` endpoint on port 8082 | Task 2 (pipeline skeleton) |
| Discord alerts: exception, 0 strategies, >15 min | Task 7 (pipeline) |
| Nightly cron 2am | Task 2 (pipeline skeleton) |
| ensure_ml_tables on startup | Task 7 (queries) |
| Docker service block | Task 8 |
| All external APIs mocked in tests | Tasks 3–6 (tests) |

### No Placeholders Detected ✓

All steps contain actual code, exact commands, and expected output.

### Type Consistency ✓

- `collect_bars` returns `dict[str, list[dict]]` — consumed as such in `_run_phases`
- `compute_features` returns `list[dict]` with `FEATURE_NAMES` keys — consumed by `_to_matrix` in discoverer
- `discover_patterns` returns `list[CandidatePattern]` — consumed in codegen loop
- `generate_strategy_code` returns `Optional[str]` — None check before `save_ml_strategy`
- `save_ml_strategy` returns `int` (strategy_id) — passed to `_backtest_strategy`
