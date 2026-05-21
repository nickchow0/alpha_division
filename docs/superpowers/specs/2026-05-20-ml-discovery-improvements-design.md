# ML Discovery Improvements — Design Spec

**Date:** 2026-05-20
**Status:** Approved

---

## Goal

Fix the ML pipeline so it reliably discovers strategies and promotes them through to candidate status. Two root causes are addressed:

1. **Data collection is broken** — yfinance batch-download crashes on certain tickers and the symbol list contains delisted stocks, resulting in 0 symbols processed on every run.
2. **Feature→snapshot mismatch** — Phase 3 discovers patterns using 26 features but the codegen prompt constrains Claude to 8 snapshot keys, 2 of which (`volume`, `volume_avg`) don't even exist in the live snapshot. Claude is forced to write a different strategy than what was discovered, which explains poor promotion rates.

A third improvement adds a pre-backtest replay gate that catches broken strategies before they hit the Research API.

---

## Changes Overview

| Area | Change |
|---|---|
| `services/data/indicators.py` | Compute full 26-feature set instead of 5 indicators |
| `services/data/main.py` | Include all new indicators + `volume` in published snapshot |
| `services/ml/collector.py` | Fix `KeyError` crash on yfinance batch downloads |
| `services/ml/codegen.py` | Update prompt to list all 26 snapshot keys |
| `services/ml/pipeline.py` | Add replay gate between Phase 4 and Phase 5 |
| `config.toml` | Remove 3 delisted symbols, add 3 replacements |

---

## Section 1 — Data Collection Fix + Symbol List

### yfinance KeyError fix (`collector.py`)

The current crash occurs when `yf.download()` is called for a single symbol in batch mode and returns without that ticker in `shared._DFS`. The fix wraps the result lookup in a `try/except KeyError` that treats missing tickers as a skip (same as the existing `no bars returned` path) rather than allowing it to propagate up and kill the thread.

### Symbol list audit

Three symbols are removed from `config.toml` due to reliable yfinance failures:

| Symbol | Reason | Replacement |
|---|---|---|
| `WOLF` | Wolfspeed filed for bankruptcy 2024, delisted | `NVDA` |
| `SNDK` | SanDisk spinoff, not trading under this ticker | `AMD` |
| `SMCI` | Accounting investigation, intermittently unavailable | `TSLA` |

`S` (SentinelOne) and `SITM` are kept — they appear to be yfinance version issues that the KeyError fix resolves.

---

## Section 2 — Expanding the Live Snapshot (Approach B)

### `data/indicators.py`

`calculate_indicators()` is expanded to compute the full feature set used by the ML discovery model. The function requires at least 210 bars (up from 50) to support SMA 200 and 52-week range calculations.

New indicators computed:

| Group | Keys |
|---|---|
| RSI variants | `rsi_7`, `rsi_14` (aliased as `rsi`), `rsi_21` |
| MACD | `macd_line`, `macd_signal`, `macd_hist` |
| Volatility | `atr_14`, `bb_width`, `dist_bb_upper`, `dist_bb_lower` |
| Volume | `vol_zscore`, `vol_ratio` |
| Momentum | `mom_5d`, `mom_10d`, `mom_20d` |
| SMA distances | `dist_sma10`, `dist_sma20`, `dist_sma50`, `dist_sma200` |
| Regime | `dist_52w_high`, `dist_52w_low`, `day_of_week` |

Existing keys (`rsi`, `sma20`, `sma50`, `sma20_prev`, `sma20_prev2`) are preserved unchanged — `filters.py` and the existing Claude prompt depend on them.

### `data/main.py`

The snapshot dict in `_fetch_and_publish_price()` is updated to include:
- `volume` (the raw volume of the latest bar — previously omitted entirely)
- All new indicator keys returned by the expanded `calculate_indicators()`

The `fetch_bars()` call already returns OHLCV; volume is already available, just never included in the snapshot.

### `ml/codegen.py`

The `_build_prompt()` function is updated to list all 26 feature keys plus `price` and `volume` as available snapshot keys, replacing the old 8-key list. The constraint section becomes:

```
You MUST use ONLY these snapshot keys (no others exist):
  price, volume,
  rsi_7, rsi_14, rsi_21,
  mom_5d, mom_10d, mom_20d,
  sma_10, sma_20, sma_50, sma_200,
  dist_sma10, dist_sma20, dist_sma50, dist_sma200,
  atr_14, bb_width, dist_bb_upper, dist_bb_lower,
  vol_zscore, vol_ratio,
  macd_line, macd_signal, macd_hist,
  dist_52w_high, dist_52w_low, day_of_week
```

Note: `sma_10`, `sma_20`, `sma_50`, `sma_200` are raw absolute SMA values (stock-price scale, e.g. 148.5). Prefer `dist_sma*` variants (normalised % distance from price) when writing cross-symbol strategies — they generalise across stocks where raw SMA values do not.

The dry-run synthetic snapshots in `_DRY_RUN_SNAPSHOTS` are updated to include all 28 keys so validation continues to work.

---

## Section 3 — Pre-Backtest Replay Gate (Approach C)

### Design

A new `validate_against_pattern()` function is added to `pipeline.py` (or a new `validator.py`). It runs between Phase 4 (codegen) and Phase 5 (backtest) as a cheap in-process quality gate.

**Inputs:**
- `code: str` — the generated strategy code
- `pattern: CandidatePattern` — the source pattern (contains `example_count`, `rule_description`, etc.)
- `pattern_rows: list[dict]` — the historical feature rows that defined the pattern (passed through from Phase 3)

**Logic:**

```
1. exec() the code in a sandboxed namespace (same as codegen validation)
2. For each feature row in pattern_rows, call generate_signal(row)
3. Count:
   - fires     = rows where decision != "hold"
   - buy_fires = rows where decision == "buy"
4. signal_rate = fires / len(pattern_rows)
5. buy_rate    = buy_fires / fires  (if fires > 0, else 0)

Gate passes if:
  signal_rate >= 0.20   (fires on ≥20% of the pattern's historical examples)
  buy_rate    >= 0.40   (≥40% of fires are buys, not just sells)
```

If the gate fails, the strategy is logged at WARNING level and skipped — it does not proceed to Phase 5 and is not saved to the `strategies` table.

### Why these thresholds

- **Signal rate 20%**: a strategy that fires on fewer than 1-in-5 of the bars that defined the pattern almost certainly isn't implementing the pattern — it's returning `hold` for everything.
- **Buy rate 40%**: prevents strategies that only ever sell from being counted as viable.

Both thresholds are configurable via `config.toml` (`min_replay_signal_rate`, `min_replay_buy_rate`) so they can be tuned without a code change.

### Data flow change in `pipeline.py`

Phase 3 currently returns `list[CandidatePattern]`. To support the replay gate, the top-N selection step also returns the corresponding filtered feature rows for each pattern. The in-memory `features_by_symbol` dict is already available at this point — no extra I/O needed.

---

## Failure Modes

| Failure | Behaviour |
|---|---|
| yfinance fails for a symbol | Log warning, skip symbol, continue with remaining symbols |
| `calculate_indicators()` returns None (< 210 bars) | Log warning, skip symbol — same as current behaviour |
| Replay gate rejects a strategy | Log warning with signal_rate and buy_rate, skip strategy, continue |
| All strategies rejected by replay gate | Discord alert: "ML pipeline: X patterns found, 0 passed replay gate" |

---

## Testing

| File | What's tested |
|---|---|
| `data/tests/test_indicators.py` | All 26 new indicators computed correctly; returns None for < 210 bars |
| `data/tests/test_publisher.py` | Snapshot includes new keys (update existing field assertion) |
| `ml/tests/test_codegen.py` | Updated dry-run snapshots have all 28 keys; prompt lists all keys |
| `ml/tests/test_pipeline.py` (new) | Replay gate passes valid strategies; rejects always-hold code; rejects sell-only code |

All external APIs remain mocked.

---

## Configuration additions (`config.toml`)

```toml
[ml]
# ... existing keys unchanged ...
min_replay_signal_rate = 0.20
min_replay_buy_rate    = 0.40
```

---

## What This Is Not

- Not changing the backtest promotion thresholds — those stay at Sharpe ≥ 0.5, win rate ≥ 45%, drawdown ≤ 20%
- Not changing the discovery model (DT + k-means, thresholds, lookback windows)
- Not changing the Research service or execution service
