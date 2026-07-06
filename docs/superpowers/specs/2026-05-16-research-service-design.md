# Research Service — Design Spec

## Goal

Build a `research` Docker service that lets Claude generate trading strategy hypotheses as Python code, backtest them on historical data, and surface passing strategies to a human approval queue before anything touches the live trading pipeline.

## Scope

This spec covers **sub-projects 1 and 2 only**: the strategy framework (interface, sandboxing, registry) and the backtesting engine (simulation, metrics, data fetchers). The hypothesis generation loop (Claude-driven), trigger system (scheduled/reactive), and live deployment pipeline are separate sub-projects built on top of this foundation.

---

## Architecture

A new `services/research/` Docker service — Flask, port 8081, same pattern as the dashboard. Shares the Postgres database via three new tables. Gets a "Research" nav link in `base.html`.

### Components

| File | Responsibility |
|---|---|
| `services/research/main.py` | Flask app — routes, health check |
| `services/research/strategy.py` | Strategy interface definition, AST validator, sandboxed executor |
| `services/research/backtester.py` | Vectorized simulation engine, metrics computation |
| `services/research/data.py` | yfinance (fast) + Alpaca (validation) historical bar fetchers |
| `services/research/queries.py` | Postgres queries for strategy registry and backtest results |
| `services/research/templates/research.html` | Strategy browser UI |
| `services/research/templates/candidates.html` | Approval queue UI |
| `services/research/static/` | CSS/JS (extends dashboard style) |
| `services/research/tests/` | Unit tests |
| `db/migrations/003_research_tables.sql` | New Postgres tables |

### Service boundaries

- **Read/write**: `strategies`, `backtest_runs`, `backtest_trades` tables
- **Read-only**: `decisions`, `signals`, `trades` (live data, used for reactive analysis in future sub-projects)
- **No write access** to the execution pipeline — approved strategies are deployed via a separate explicit action

---

## Strategy Interface

Every strategy is a single Python function with a fixed signature:

```python
def generate_signal(snapshot: dict) -> dict:
    """
    snapshot keys (all floats):
        price       — current bar close price
        rsi         — RSI(14)
        sma20       — 20-bar simple moving average
        sma50       — 50-bar simple moving average
        sma20_prev  — SMA20 from previous bar
        sma20_prev2 — SMA20 from two bars ago
        volume      — current bar volume
        volume_avg  — 20-bar average volume

    Returns:
        decision:   "buy" | "sell" | "hold"
        confidence: float 0.0–1.0
        reasoning:  str (1–2 sentence explanation)
    """
```

Example:

```python
def generate_signal(snapshot: dict) -> dict:
    rsi = snapshot["rsi"]
    price = snapshot["price"]
    sma50 = snapshot["sma50"]
    volume = snapshot["volume"]
    volume_avg = snapshot["volume_avg"]

    if rsi < 35 and price > sma50 and volume > volume_avg * 1.5:
        return {
            "decision": "buy",
            "confidence": 0.78,
            "reasoning": "Oversold RSI with price above SMA50 and volume spike confirmation."
        }
    return {"decision": "hold", "confidence": 0.5, "reasoning": "No signal conditions met."}
```

### Sandboxing

Two layers — neither requires RestrictedPython:

**Layer 1 — AST analysis at save time.** Walk the syntax tree before storing any strategy. Reject if any of the following are present: `import`, `__import__`, `open`, `exec`, `eval`, `os`, `sys`, `socket`, `subprocess`. Caught before the code ever runs.

**Layer 2 — Timeout at execution time.** Each `generate_signal()` call runs inside a `concurrent.futures.ThreadPoolExecutor` with a 2-second timeout. Infinite loops and slow code are killed, not hung.

The strategy source and a SHA256 hash of the source are both stored in Postgres. The hash provides an audit trail — any backtest result can be tied back to the exact code that produced it.

---

## Backtesting Engine

### Algorithm

Vectorized simulation — process all bars sequentially, no look-ahead:

1. Fetch historical bars for `symbol` over `[start_date, end_date]`
2. Compute indicators for each bar using `calculate_indicators()` (reused from `services/data/indicators.py`)
3. Call `generate_signal(snapshot)` for each bar in order
4. Simulate trades using the rules below
5. Compute metrics
6. Store run + individual trades in Postgres

### Trade simulation rules

| Rule | Value |
|---|---|
| Entry fill | Next bar's open after buy/sell signal (prevents look-ahead bias) |
| Exit — signal | Next bar's open after opposing signal |
| Exit — stop loss | When price drops more than `stop_loss_pct` below entry |
| Exit — max hold | After `max_hold_bars` bars regardless of signal |
| Direction | Long only (matches live system; no short selling) |
| Slippage | 0.05% of trade value per side |

### Position sizing

Confidence-scaled: `position_size = confidence × max_position_pct × portfolio_value`

With default `max_position_pct = 15%`:
- 0.65 confidence → 9.75% of portfolio
- 0.80 confidence → 12.0% of portfolio
- 0.90 confidence → 13.5% of portfolio

### Configurable parameters (per backtest run)

| Parameter | Default |
|---|---|
| `symbol` | required |
| `start_date` | required |
| `end_date` | required |
| `data_source` | `"yfinance"` |
| `initial_capital` | `100000` |
| `max_position_pct` | `0.15` |
| `stop_loss_pct` | `0.05` |
| `max_hold_bars` | `20` |

### Performance metrics

| Metric | Computation |
|---|---|
| Total return % | `(final_value - initial_capital) / initial_capital × 100` |
| Sharpe ratio | Annualised using per-trade returns, risk-free rate = 0 |
| Max drawdown % | Largest peak-to-trough decline in portfolio value during period |
| Win rate % | Trades closed with `pnl > 0` / total closed trades × 100 |
| Trade count | Total executed trades (entries) |
| Avg hold (bars) | Mean bars between entry and exit across all trades |

### Data paths

| Path | Source | Bar size | Use for |
|---|---|---|---|
| Fast | yfinance | Daily | Multi-year hypothesis research, rapid iteration |
| Validation | Alpaca historical API | 15-min | Final check before candidate queue — same resolution as live system |

Both paths pipe bars through the same `calculate_indicators()` function. A strategy must pass the validation (Alpaca) backtest to become a `candidate`.

---

## Database Schema

```sql
-- Migration 003: Research service tables

CREATE TABLE strategies (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    hypothesis      TEXT NOT NULL,       -- what Claude/user expected this strategy to do
    code            TEXT NOT NULL,       -- Python source of generate_signal()
    code_hash       TEXT NOT NULL,       -- SHA256 of code for audit trail
    status          TEXT NOT NULL DEFAULT 'draft',
    -- status values: draft | testing | candidate | approved | live | retired
    triggered_by    TEXT NOT NULL,       -- 'manual' | 'scheduled' | 'reactive'
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE backtest_runs (
    id                  SERIAL PRIMARY KEY,
    strategy_id         INTEGER REFERENCES strategies(id),
    symbol              TEXT NOT NULL,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    data_source         TEXT NOT NULL,           -- 'yfinance' | 'alpaca'
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
    critique            TEXT,                    -- Claude's written critique (populated in sub-project 3)
    ran_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE backtest_trades (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER REFERENCES backtest_runs(id),
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,               -- 'buy' | 'sell'
    entry_bar       INTEGER NOT NULL,            -- bar index in historical series
    exit_bar        INTEGER,
    entry_price     DECIMAL(10,4),
    exit_price      DECIMAL(10,4),
    position_size   DECIMAL(10,4),              -- $ allocated (confidence-scaled)
    pnl             DECIMAL(10,4),
    exit_reason     TEXT                        -- 'signal' | 'stop_loss' | 'max_hold'
);
```

### Strategy lifecycle

```
draft → testing → candidate → approved → live
                                       ↘ retired (any status)
```

- `draft`: just created, AST validated, not yet backtested
- `testing`: backtest in progress or completed on yfinance data
- `candidate`: passed both yfinance and Alpaca validation backtests with Sharpe ≥ 0.5, win rate ≥ 45%, and max drawdown ≤ 20%; visible in approval queue
- `approved`: human approved, ready to deploy
- `live`: active in the analysis service as Stage 1 filter (only one strategy is `live` at a time)
- `retired`: superseded or manually retired

---

## API Routes

| Method | Route | Description |
|---|---|---|
| `GET` | `/health` | Health check — `{"status": "ok"}` |
| `POST` | `/api/strategies` | Save new strategy — validates AST, stores code + hash, status=`draft` |
| `GET` | `/api/strategies` | List all strategies with status and latest backtest metrics |
| `POST` | `/api/strategies/<id>/backtest` | Trigger backtest — body: `{symbol, start_date, end_date, data_source, ...params}` |
| `GET` | `/api/strategies/<id>/runs` | All backtest runs for a strategy with metrics |
| `POST` | `/api/strategies/<id>/approve` | Promote `candidate` → `approved` |
| `POST` | `/api/strategies/<id>/retire` | Move any status → `retired` |
| `GET` | `/research` | HTML — strategy browser page |
| `GET` | `/candidates` | HTML — approval queue page |

---

## Dashboard UI

### `/research` — Strategy Browser

Table of all strategies with: status badge, `triggered_by`, hypothesis summary, latest Sharpe/win rate/max drawdown. Clicking a row expands to show full strategy code, backtest history table, and a "Run Backtest" form (symbol, date range, data source). Linked from the main nav as "Research" between Analysis and Watchlist.

### `/candidates` — Approval Queue

Shows only strategies with status `candidate`, sorted by Sharpe ratio descending. Each card shows:
- Hypothesis text
- Strategy code (syntax-highlighted)
- Metrics table with yfinance and Alpaca runs side by side
- Equity curve (Chart.js line chart of portfolio value over backtest period)
- Claude's critique (populated in sub-project 3; blank for now)
- **Approve** button → `POST /api/strategies/<id>/approve`
- **Retire** button → `POST /api/strategies/<id>/retire`

Both pages extend `base.html` and follow the existing dark-theme dashboard style.

---

## Live Deployment Integration

Out of scope for this sub-project but designed here so the strategy interface is built correctly.

Approved strategies plug into the analysis service as a replacement for the hard-coded Stage 1 filter (`filters.py`). Claude's Stage 2 decision is unchanged.

```
Current:  passes_technical_filter() → call_claude()
Future:   strategy.generate_signal() → call_claude()
```

The analysis service polls the `strategies` table every 60 seconds. If a `live` strategy exists, it loads and executes it as Stage 1. If none exists, it falls back to the hard-coded filter — no disruption to live trading during development.

Only one strategy can have status `live` at a time. Deploying a new strategy automatically retires the previous one.

---

## Error Handling

| Failure | Behaviour |
|---|---|
| AST validation fails at save | `400` response with specific rejection reason; strategy not stored |
| `generate_signal()` raises exception during backtest | That bar is treated as `hold`; exception logged; backtest continues |
| `generate_signal()` times out (> 2s) | Same as exception — treated as `hold` for that bar |
| `generate_signal()` returns invalid schema | Backtest aborts; run stored with `trade_count=0` and error noted |
| yfinance fetch fails | `500` response; no run stored |
| Alpaca fetch fails | `500` response; no run stored; strategy stays in `testing` status |
| Backtest produces zero trades | Run stored normally; metrics will be null; not promoted to `candidate` |

---

## Testing

**Unit tests (`services/research/tests/test_strategy.py`):**
- AST validator blocks `import os`, `open()`, `exec()`, `eval()`, `__import__`
- AST validator passes valid strategy code
- Executor returns correct decision dict
- Executor enforces timeout (mock slow function)

**Unit tests (`services/research/tests/test_backtester.py`):**
- Confidence-scaled position sizing correct at various confidence levels
- Stop loss exits at correct bar
- Max hold exits at correct bar
- Signal-based exit at correct bar
- Metrics computed correctly for known trade sequence
- Zero-trade backtest handled gracefully

**Unit tests (`services/research/tests/test_data.py`):**
- yfinance fetcher returns bars in expected format
- Alpaca fetcher returns bars in expected format (mocked)

**Unit tests (`services/research/tests/test_main.py`):**
- `POST /api/strategies` with valid code → 201
- `POST /api/strategies` with `import os` → 400
- `POST /api/strategies/<id>/backtest` → 200, run stored
- `GET /research` → 200
- `GET /candidates` → 200
