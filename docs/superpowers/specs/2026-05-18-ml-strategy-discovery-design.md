# ML Strategy Discovery Pipeline — Design Spec

**Date:** 2026-05-18
**Status:** Approved

---

## Goal

Build an autonomous ML pipeline (`services/ml/`) that runs nightly, discovers trading patterns from historical data across 26 mid-large cap symbols, generates `generate_signal()` strategy code via Claude, backtests each strategy, and auto-promotes winners to the Research Service candidate queue — all without human intervention.

---

## Symbols Universe

26 symbols in the $10B–$500B market cap range, spanning AI software, chipmakers, and high-volatility mid-caps:

```toml
symbols = [
  "CRWD", "SNOW", "DDOG", "SHOP", "MELI", "COIN", "UBER", "AXON",
  "PLTR", "AI", "BBAI", "SOUN", "IONQ", "RXRX", "GTLB", "PATH",
  "S", "CPNG", "MRVL", "MPWR", "WOLF", "SITM", "ONTO", "ALAB",
  "SMCI", "SNDK"
]
```

---

## Architecture

A new Docker service (`services/ml/`) runs as a nightly batch pipeline. It has no HTTP API — it is a pure pipeline worker triggered by an internal cron job at 2am. It shares the existing Postgres database via `shared.db` and calls the Anthropic API directly for strategy codegen.

```
services/ml/
├── Dockerfile
├── requirements.txt
├── pipeline.py       # entrypoint — orchestrates all 5 phases
├── collector.py      # phase 1: fetch and cache OHLCV bars
├── features.py       # phase 2: compute extended indicator feature vectors
├── discoverer.py     # phase 3: decision tree + k-means pattern discovery
├── codegen.py        # phase 4: Claude API → generate_signal() code
└── tests/
    ├── test_features.py
    ├── test_discoverer.py
    └── test_codegen.py
```

Generated strategies are written to the existing `strategies` and `backtest_runs` tables — no new schema required. All ML-generated strategies have `triggered_by = "ml_discovery"`.

Two new DB tables are required: `ml_bars` (bar cache) and `ml_runs` (pipeline run history). These are created via a new migration `db/migrations/004_ml_tables.sql`.

---

## Phase 1: Data Collection

- Fetches daily OHLCV bars via yfinance for all 26 symbols
- **Momentum models:** 1 year lookback (365 days)
- **Regime/cluster models:** 5 years lookback (1825 days)
- Bars are cached in Postgres (`ml_bars` table keyed by symbol + date) — only fetches new bars on subsequent runs
- Runs in parallel across symbols (ThreadPoolExecutor)

---

## Phase 2: Feature Engineering

Each bar gets a feature vector computed from raw OHLCV data:

| Group | Features |
|---|---|
| Momentum | RSI 7, RSI 14, RSI 21; price momentum 5d, 10d, 20d |
| Trend | SMA 10, 20, 50, 200; price distance from each SMA as % |
| Volatility | ATR 14; Bollinger Band width; distance from upper/lower band |
| Volume | Volume z-score (20d rolling); volume ratio (today vs 5d avg) |
| MACD | MACD line, signal line, histogram |
| Regime | Distance from 52-week high/low; day of week (0–4) |

**Labels:**
- Decision tree: 10-bar forward return, binary label (1 = top 30% of returns for that symbol, 0 = otherwise)
- Clustering: no label — unsupervised; clusters profiled by average forward return after fitting

---

## Phase 3: Pattern Discovery

### Decision Tree (momentum, 1yr data)

- Trains a `DecisionTreeClassifier` (max depth 4) per symbol, then one cross-symbol tree on all symbols combined
- Max depth 4 keeps rules shallow and human-readable
- Each leaf node is extracted as a candidate pattern if it passes:
  - ≥ 30 historical bar examples
  - Average 10-bar forward return ≥ 1.5%
  - Win rate ≥ 45%
- Rule extracted as a condition chain: e.g. `RSI_14 < 38 AND price_vs_sma20 > 0.02 AND volume_zscore > 1.3`

### K-Means Clustering (regime, 5yr data)

- Fits `KMeans` with k=10 on the full feature vector across all symbols and 5 years of bars
- Each cluster is profiled: average forward return, win rate, example count, centroid feature values
- Candidate clusters must pass:
  - ≥ 50 historical bar examples
  - Average 10-bar forward return ≥ 1.5%
  - Win rate ≥ 45%
- Cluster profile describes the market condition in plain terms from centroid values

### Top-5 Selection

All candidate patterns from both models are ranked by the Sharpe ratio of their historical example returns. The top 5 overall are passed to codegen. If fewer than 5 pass the gate, fewer strategies are generated — the run does not fail.

---

## Phase 4: Strategy Codegen

Claude Sonnet generates a `generate_signal()` function for each discovered pattern.

**Prompt structure:**
```
Pattern type: decision_tree | cluster
Rule/profile: <human-readable description>
Historical performance: N examples, avg return X%, win rate Y%

Write a generate_signal(snapshot) function using ONLY these snapshot keys:
  price, rsi, sma20, sma50, sma20_prev, sma20_prev2, volume, volume_avg

Return format: {"decision": "buy"|"sell"|"hold", "confidence": 0.0–1.0, "reasoning": str}
No imports. No external calls.
```

**Quality gates (applied before saving):**
1. AST validation via existing `validate_strategy_code()`
2. Function named `generate_signal` exists in output
3. Dry-run on 3 synthetic snapshots — must return valid schema without raising

**Retry logic:** if validation fails, the error message is appended to the prompt and Claude retries once. If it fails again, the pattern is skipped and logged.

**On success:** strategy saved to `strategies` table with `status = "testing"`, `triggered_by = "ml_discovery"`, and a `description` summarising the pattern and its historical performance.

---

## Phase 5: Auto-Backtest & Promote

Each generated strategy is immediately backtested using the existing backtest engine:
- **yfinance** backtest: 2yr daily bars on the originating symbol
- **Alpaca** backtest: if Alpaca credentials are configured, 15-min bars on the same symbol

Promotion thresholds (same as research service):
- Sharpe ≥ 0.5
- Win rate ≥ 45%
- Max drawdown ≤ 20%
- Trade count > 0

Strategies that pass on the Alpaca backtest are promoted to `status = "candidate"` and appear in the Research Service at `/candidates` for human review before any live trading.

---

## Observability

**Health endpoint:** `GET /health` → `{"status": "ok"}` — monitored by the existing watchdog.

**Logging:** structured logs per phase at INFO level. Each phase logs symbol count, patterns found, strategies generated, candidates promoted.

**Alerts:** Discord alert (via existing alerter) if:
- Pipeline raises an unhandled exception
- 0 strategies are generated after a full run
- Any phase takes > 15 minutes

**Bar cache:** fetched bars are stored in `ml_bars` to avoid re-fetching on subsequent runs:
```sql
CREATE TABLE IF NOT EXISTS ml_bars (
    id      SERIAL PRIMARY KEY,
    symbol  TEXT NOT NULL,
    bar_date DATE NOT NULL,
    open    FLOAT, high FLOAT, low FLOAT, close FLOAT, volume BIGINT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(symbol, bar_date)
);
```

**Run record:** each execution writes one row to `ml_runs`:
```sql
CREATE TABLE IF NOT EXISTS ml_runs (
    id              SERIAL PRIMARY KEY,
    ran_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbols_processed INTEGER,
    patterns_found  INTEGER,
    strategies_generated INTEGER,
    candidates_promoted INTEGER,
    duration_seconds FLOAT,
    error           TEXT
);
```

---

## Configuration (`config.toml`)

```toml
[ml]
symbols = [
  "CRWD", "SNOW", "DDOG", "SHOP", "MELI", "COIN", "UBER", "AXON",
  "PLTR", "AI", "BBAI", "SOUN", "IONQ", "RXRX", "GTLB", "PATH",
  "S", "CPNG", "MRVL", "MPWR", "WOLF", "SITM", "ONTO", "ALAB",
  "SMCI", "SNDK"
]
lookback_days_momentum = 365
lookback_days_regime = 1825
max_strategies_per_run = 5
min_forward_return_pct = 1.5
min_examples = 30
min_win_rate_pct = 45.0
cron_schedule = "0 2 * * *"
```

---

## Docker Integration

Added to `docker-compose.yml` following the same pattern as other services:

```yaml
ml:
  build: ./services/ml
  restart: always
  depends_on:
    postgres:
      condition: service_healthy
  env_file: .env
  environment:
    SERVICE_NAME: ml
  volumes:
    - ./shared:/app/shared
    - ./config.toml:/app/config.toml:ro
    - logs:/var/log/alphadivision
```

---

## Runtime Budget

| Phase | Estimated time |
|---|---|
| Data collection (26 symbols, cached) | ~2 min |
| Feature engineering | ~30s |
| Pattern discovery (trees + clustering) | ~1 min |
| Codegen (5 patterns × Claude API) | ~2 min |
| Backtest (5 strategies × 2 sources) | ~3 min |
| **Total** | **~9 min** |

---

## Testing

- `test_features.py` — unit tests for each indicator computation; assert no NaN in output, correct shape
- `test_discoverer.py` — train on synthetic bars, assert at least one pattern found, assert rule is a non-empty string
- `test_codegen.py` — mock Claude API response, assert generated code passes AST validation and dry-run
- All external APIs mocked (yfinance, Anthropic, Alpaca)

---

## What This Is Not

- Not a live trading system — all output goes to the Research Service candidate queue for human approval
- Not replacing the existing Claude-driven analysis service — that continues to run in real-time
- Not a neural/sequence model — that is a future sub-project if decision trees + clustering prove insufficient
