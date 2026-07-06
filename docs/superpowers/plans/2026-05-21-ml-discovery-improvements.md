# ML Discovery Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the ML pipeline so it reliably discovers strategies and promotes them to candidate status by repairing data collection, closing the feature→snapshot gap across all three services, and adding a pre-backtest replay gate.

**Architecture:** Three root causes are fixed in parallel dependency order: (1) yfinance/symbol-list breakage in the ML collector; (2) a feature→snapshot mismatch that spans the data service, research backtester, and ML codegen — all three must expose the same 26+ indicator keys so generated strategy code is actually executable at every stage; (3) a replay gate in the ML pipeline that validates generated code against the historical rows that defined each pattern before hitting the Research API.

**Tech Stack:** Python, yfinance, pandas, ta (ta.volatility newly used in data/research services), scikit-learn, anthropic SDK

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `services/ml/requirements.txt` | Modify | Upgrade yfinance + anthropic to fix crashes |
| `services/research/requirements.txt` | Modify | Upgrade yfinance to fix data fetching |
| `config.toml` | Modify | Replace 3 delisted symbols; add replay threshold keys |
| `services/ml/collector.py` | Modify | Catch `KeyError` from yfinance batch download |
| `services/ml/tests/test_collector.py` | Modify | Add test for KeyError resilience |
| `services/data/fetchers.py` | Modify | Fetch 250 bars / 400 days for SMA200 + 52w range |
| `services/data/indicators.py` | Rewrite | Compute full 26-feature set + volume |
| `services/data/tests/test_indicators.py` | Rewrite | Test all 28 keys; update 210-bar minimum |
| `services/data/main.py` | Modify | Spread all indicator keys into published snapshot |
| `services/data/tests/test_publisher.py` | Modify | Update required-fields assertion |
| `services/research/backtester.py` | Modify | Expand snapshot to all 26 features per bar |
| `services/research/tests/test_backtester.py` | Modify | Update bar count threshold; add ML key assertions |
| `services/ml/codegen.py` | Modify | Update prompt + all 28-key dry-run snapshots |
| `services/ml/tests/test_codegen.py` | Modify | Fix test referencing removed `volume_avg` key |
| `services/ml/discoverer.py` | Modify | Add `rows` field to `CandidatePattern`; capture per-pattern rows |
| `services/ml/tests/test_discoverer.py` | Modify | Assert patterns have non-empty `rows` |
| `services/ml/validator.py` | Create | Replay gate: signal rate + buy rate checks |
| `services/ml/tests/test_validator.py` | Create | Gate pass/fail tests for hold, sell-only, and RSI strategies |
| `services/ml/pipeline.py` | Modify | Call replay gate between codegen and backtest |

---

## Task 1: Fix dependencies, symbol list, and yfinance KeyError

**Files:**
- Modify: `services/ml/requirements.txt`
- Modify: `services/research/requirements.txt`
- Modify: `config.toml`
- Modify: `services/ml/collector.py`
- Modify: `services/ml/tests/test_collector.py`

- [ ] **Step 1: Write a failing test for KeyError resilience**

Add to `services/ml/tests/test_collector.py`:

```python
def test_fetch_yfinance_returns_empty_on_key_error():
    """KeyError raised inside yf.download (yfinance batch-mode bug) returns []."""
    with patch("collector.yf.download", side_effect=KeyError("SITM")):
        bars = _fetch_yfinance("SITM", date(2024, 1, 1), date(2024, 1, 10))
    assert bars == []
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd services/ml && python -m pytest tests/test_collector.py::test_fetch_yfinance_returns_empty_on_key_error -v
```

Expected: `FAILED` — `KeyError: 'SITM'` propagates uncaught.

- [ ] **Step 3: Fix `_fetch_yfinance` to return `[]` on `KeyError`**

In `services/ml/collector.py`, wrap the `yf.download()` call:

```python
def _fetch_yfinance(symbol: str, start: date, end: date) -> list[dict]:
    """Fetch OHLCV bars from yfinance for the given date range.

    Returns a list of dicts with keys: date, open, high, low, close, volume.
    Returns [] if no data is returned (e.g. market holiday, bad symbol, yfinance bug).
    """
    try:
        df = yf.download(
            symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except (KeyError, Exception) as exc:
        log.warning("%s: yfinance download raised %s: %s", symbol, type(exc).__name__, exc)
        return []
    # Defensive: recent yfinance versions may return MultiIndex columns for single tickers
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd services/ml && python -m pytest tests/test_collector.py::test_fetch_yfinance_returns_empty_on_key_error -v
```

Expected: `PASSED`

- [ ] **Step 5: Upgrade yfinance and anthropic in ML requirements**

Replace the pinned versions in `services/ml/requirements.txt`:

```
psycopg2-binary==2.9.9
redis==5.0.4
flask==3.0.3
schedule==1.2.2
yfinance>=0.2.50
pandas==2.2.2
numpy==1.26.4
scikit-learn==1.4.2
anthropic>=0.40.0
google-generativeai==0.8.3
httpx==0.27.2
requests==2.32.3
python-dotenv==1.0.1
ta==0.11.0
```

- [ ] **Step 6: Upgrade yfinance in research requirements**

In `services/research/requirements.txt`, change `yfinance==0.2.40` to `yfinance>=0.2.50`.

- [ ] **Step 7: Update symbol list in config.toml**

Replace the `symbols` list under `[ml]`:

```toml
symbols = [
  "CRWD", "SNOW", "DDOG", "SHOP", "MELI", "COIN", "UBER", "AXON",
  "PLTR", "AI", "BBAI", "SOUN", "IONQ", "RXRX", "GTLB", "PATH",
  "S", "CPNG", "MRVL", "MPWR", "SITM", "ONTO", "ALAB",
  "NVDA", "AMD", "TSLA",
]
```

Removed: `WOLF` (bankrupt), `SNDK` (renamed), `SMCI` (accounting issues).
Added: `NVDA`, `AMD`, `TSLA`.

- [ ] **Step 8: Run full ML test suite**

```bash
cd services/ml && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add services/ml/requirements.txt services/research/requirements.txt \
        config.toml services/ml/collector.py services/ml/tests/test_collector.py
git commit -m "fix: upgrade yfinance, refresh ML symbol list, catch KeyError in collector"
```

---

## Task 2: Expand data service to 250-bar fetch and full 26-feature indicators

**Files:**
- Modify: `services/data/fetchers.py`
- Rewrite: `services/data/indicators.py`
- Rewrite: `services/data/tests/test_indicators.py`

- [ ] **Step 1: Write failing tests for the new indicators**

Replace `services/data/tests/test_indicators.py` entirely:

```python
import pytest
from indicators import calculate_indicators


def _make_bars(prices: list[float]) -> list[dict]:
    """Build bars list — h/l are ±1% of close, volume is constant."""
    return [
        {
            "t": f"2024-01-{(i % 28) + 1:02d}",
            "o": p,
            "h": round(p * 1.01, 4),
            "l": round(p * 0.99, 4),
            "c": p,
            "v": 1_000_000,
        }
        for i, p in enumerate(prices)
    ]


# ---------------------------------------------------------------------------
# Minimum bar threshold
# ---------------------------------------------------------------------------

def test_returns_none_when_fewer_than_210_bars():
    bars = _make_bars([100.0] * 209)
    assert calculate_indicators(bars) is None


def test_returns_none_when_zero_bars():
    assert calculate_indicators([]) is None


def test_returns_dict_when_210_bars():
    bars = _make_bars([float(100 + i * 0.01) for i in range(210)])
    result = calculate_indicators(bars)
    assert result is not None
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Legacy keys present (backward-compat for filters.py)
# ---------------------------------------------------------------------------

def test_legacy_keys_present():
    bars = _make_bars([float(100 + i * 0.01) for i in range(210)])
    result = calculate_indicators(bars)
    assert result is not None
    for key in ("rsi", "sma20", "sma50", "sma20_prev", "sma20_prev2"):
        assert key in result, f"Missing legacy key: {key}"


# ---------------------------------------------------------------------------
# ML feature keys present
# ---------------------------------------------------------------------------

_ML_KEYS = [
    "rsi_7", "rsi_14", "rsi_21",
    "mom_5d", "mom_10d", "mom_20d",
    "sma_10", "sma_20", "sma_50", "sma_200",
    "dist_sma10", "dist_sma20", "dist_sma50", "dist_sma200",
    "atr_14", "bb_width", "dist_bb_upper", "dist_bb_lower",
    "vol_zscore", "vol_ratio",
    "macd_line", "macd_signal", "macd_hist",
    "dist_52w_high", "dist_52w_low",
    "day_of_week", "volume",
]


def test_all_ml_keys_present():
    bars = _make_bars([float(100 + i * 0.01) for i in range(210)])
    result = calculate_indicators(bars)
    assert result is not None
    for key in _ML_KEYS:
        assert key in result, f"Missing ML key: {key}"


# ---------------------------------------------------------------------------
# Value correctness
# ---------------------------------------------------------------------------

def test_rsi_14_equals_legacy_rsi():
    bars = _make_bars([float(100 + i * 0.01) for i in range(210)])
    result = calculate_indicators(bars)
    assert result is not None
    assert result["rsi_14"] == result["rsi"]


def test_sma_20_equals_legacy_sma20():
    bars = _make_bars([float(100 + i * 0.01) for i in range(210)])
    result = calculate_indicators(bars)
    assert result is not None
    assert result["sma_20"] == result["sma20"]


def test_rsi_in_valid_range():
    bars = _make_bars([float(100 + i * 0.01) for i in range(210)])
    result = calculate_indicators(bars)
    assert result is not None
    assert 0.0 <= result["rsi"] <= 100.0
    assert 0.0 <= result["rsi_7"] <= 100.0
    assert 0.0 <= result["rsi_21"] <= 100.0


def test_day_of_week_is_int_0_to_4():
    bars = _make_bars([float(100 + i * 0.01) for i in range(210)])
    result = calculate_indicators(bars)
    assert result is not None
    assert isinstance(result["day_of_week"], int)
    assert 0 <= result["day_of_week"] <= 4


def test_volume_is_int():
    bars = _make_bars([float(100 + i * 0.01) for i in range(210)])
    result = calculate_indicators(bars)
    assert result is not None
    assert isinstance(result["volume"], int)
    assert result["volume"] == 1_000_000


def test_dist_sma20_is_zero_when_price_equals_sma():
    """On a flat price series, close == SMA20 so distance is 0."""
    bars = _make_bars([100.0] * 210)
    result = calculate_indicators(bars)
    assert result is not None
    assert result["dist_sma20"] is not None
    assert abs(result["dist_sma20"]) < 0.001
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd services/data && python -m pytest tests/test_indicators.py -v
```

Expected: multiple failures — missing keys, wrong minimum bar count.

- [ ] **Step 3: Rewrite `services/data/indicators.py`**

```python
from typing import Optional

import pandas as pd
import ta.momentum
import ta.trend
import ta.volatility

_MIN_BARS = 210  # SMA200 needs 200 bars; extra headroom for rolling windows


def calculate_indicators(bars: list[dict]) -> Optional[dict]:
    """Compute the full ML feature set plus legacy snapshot keys from OHLCV bars.

    Expects bars in Alpaca format: each dict has keys t, o, h, l, c, v.
    Returns None if fewer than 210 bars are provided.
    NaN indicator values are returned as None (not float('nan')).
    """
    if len(bars) < _MIN_BARS:
        return None

    closes  = pd.Series([float(b["c"]) for b in bars])
    highs   = pd.Series([float(b["h"]) for b in bars])
    lows    = pd.Series([float(b["l"]) for b in bars])
    volumes = pd.Series([float(b["v"]) for b in bars])

    # RSI variants
    rsi_7_s  = ta.momentum.RSIIndicator(close=closes, window=7).rsi()
    rsi_14_s = ta.momentum.RSIIndicator(close=closes, window=14).rsi()
    rsi_21_s = ta.momentum.RSIIndicator(close=closes, window=21).rsi()

    # SMA
    sma_10_s  = ta.trend.SMAIndicator(close=closes, window=10).sma_indicator()
    sma_20_s  = ta.trend.SMAIndicator(close=closes, window=20).sma_indicator()
    sma_50_s  = ta.trend.SMAIndicator(close=closes, window=50).sma_indicator()
    sma_200_s = ta.trend.SMAIndicator(close=closes, window=200).sma_indicator()

    # Momentum
    mom_5d_s  = closes.pct_change(5)
    mom_10d_s = closes.pct_change(10)
    mom_20d_s = closes.pct_change(20)

    # ATR + Bollinger
    atr_14_s   = ta.volatility.AverageTrueRange(
        high=highs, low=lows, close=closes, window=14
    ).average_true_range()
    bb_ind     = ta.volatility.BollingerBands(close=closes, window=20, window_dev=2)
    bb_upper_s = bb_ind.bollinger_hband()
    bb_lower_s = bb_ind.bollinger_lband()
    bb_mid_s   = bb_ind.bollinger_mavg()

    # Volume rolling
    vol_mean_s = volumes.rolling(window=20).mean()
    vol_std_s  = volumes.rolling(window=20).std()
    vol_avg5_s = volumes.rolling(window=5).mean()

    # MACD
    macd_ind      = ta.trend.MACD(close=closes)
    macd_line_s   = macd_ind.macd()
    macd_signal_s = macd_ind.macd_signal()
    macd_hist_s   = macd_ind.macd_diff()

    # 52-week range (min_periods=50 so it computes once enough data exists)
    high_252_s = closes.rolling(window=252, min_periods=50).max()
    low_252_s  = closes.rolling(window=252, min_periods=50).min()

    def _f(val) -> Optional[float]:
        return None if pd.isna(val) else float(val)

    # Validate legacy keys are computable before returning
    rsi_valid  = rsi_14_s.dropna()
    sma20_valid = sma_20_s.dropna()
    sma50_valid = sma_50_s.dropna()
    if len(rsi_valid) == 0 or len(sma20_valid) < 3 or len(sma50_valid) == 0:
        return None

    c    = closes.iloc[-1]
    s10  = sma_10_s.iloc[-1]
    s20  = sma_20_s.iloc[-1]
    s50  = sma_50_s.iloc[-1]
    s200 = sma_200_s.iloc[-1]

    bb_up  = bb_upper_s.iloc[-1]
    bb_lo  = bb_lower_s.iloc[-1]
    bb_mid = bb_mid_s.iloc[-1]
    bb_w   = (bb_up - bb_lo) if not (pd.isna(bb_up) or pd.isna(bb_lo)) else float("nan")

    vm  = vol_mean_s.iloc[-1]
    vs  = vol_std_s.iloc[-1]
    va5 = vol_avg5_s.iloc[-1]
    v   = volumes.iloc[-1]
    vol_z = float((v - vm) / vs) if (not pd.isna(vm) and not pd.isna(vs) and vs != 0) else None
    vol_r = float(v / va5)       if (not pd.isna(va5) and va5 != 0)                    else None

    h252 = high_252_s.iloc[-1]
    l252 = low_252_s.iloc[-1]

    try:
        dow = min(pd.Timestamp(bars[-1]["t"]).weekday(), 4)
    except Exception:
        dow = 0

    return {
        # Legacy keys — backward-compat for filters.py and existing Claude prompts
        "rsi":         float(rsi_valid.iloc[-1]),
        "sma20":       float(sma20_valid.iloc[-1]),
        "sma50":       float(sma50_valid.iloc[-1]),
        "sma20_prev":  float(sma20_valid.iloc[-2]),
        "sma20_prev2": float(sma20_valid.iloc[-3]),
        # ML feature keys
        "rsi_7":        _f(rsi_7_s.iloc[-1]),
        "rsi_14":       _f(rsi_14_s.iloc[-1]),
        "rsi_21":       _f(rsi_21_s.iloc[-1]),
        "mom_5d":       _f(mom_5d_s.iloc[-1]),
        "mom_10d":      _f(mom_10d_s.iloc[-1]),
        "mom_20d":      _f(mom_20d_s.iloc[-1]),
        "sma_10":       _f(s10),
        "sma_20":       _f(s20),
        "sma_50":       _f(s50),
        "sma_200":      _f(s200),
        "dist_sma10":   float((c - s10) / s10)   if not (pd.isna(s10)  or s10  == 0) else None,
        "dist_sma20":   float((c - s20) / s20)   if not (pd.isna(s20)  or s20  == 0) else None,
        "dist_sma50":   float((c - s50) / s50)   if not (pd.isna(s50)  or s50  == 0) else None,
        "dist_sma200":  float((c - s200) / s200) if not (pd.isna(s200) or s200 == 0) else None,
        "atr_14":       _f(atr_14_s.iloc[-1]),
        "bb_width":     float(bb_w / bb_mid) if not (pd.isna(bb_w) or pd.isna(bb_mid) or bb_mid == 0) else None,
        "dist_bb_upper": float((bb_up - c) / c) if (not pd.isna(bb_up) and c != 0) else None,
        "dist_bb_lower": float((c - bb_lo) / c) if (not pd.isna(bb_lo) and c != 0) else None,
        "vol_zscore":   vol_z,
        "vol_ratio":    vol_r,
        "macd_line":    _f(macd_line_s.iloc[-1]),
        "macd_signal":  _f(macd_signal_s.iloc[-1]),
        "macd_hist":    _f(macd_hist_s.iloc[-1]),
        "dist_52w_high": float((h252 - c) / c) if (not pd.isna(h252) and c != 0) else None,
        "dist_52w_low":  float((c - l252) / c) if (not pd.isna(l252) and c != 0) else None,
        "day_of_week":  dow,
        "volume":       int(v),
    }
```

- [ ] **Step 4: Update `fetch_bars` in `services/data/fetchers.py` to return 250 bars**

Change the lookback and limit so we have enough data for SMA200 and 52-week range:

```python
def fetch_bars(symbol: str, api_key: str, secret_key: str, base_url: str) -> list[dict]:
    """
    Fetch 250 daily OHLCV bars for the given symbol from Alpaca.

    Passes an explicit start date (400 calendar days ago) so Alpaca returns
    enough bars for SMA200 and 52-week range indicators.

    Returns a list of dicts with keys: t, o, h, l, c, v.
    Raises ValueError if Alpaca returns no bars.
    """
    api = tradeapi.REST(api_key, secret_key, base_url)
    start = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    bars_resp = api.get_bars(symbol, "1Day", start=start, limit=250)
    df = bars_resp.df

    if df.empty:
        raise ValueError(f"No bars returned for {symbol}")

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

- [ ] **Step 5: Run all data service tests**

```bash
cd services/data && python -m pytest tests/ -v
```

Expected: all tests pass. If `test_fetchers.py` asserts the old `limit=60`, update that assertion to `limit=250`.

- [ ] **Step 6: Commit**

```bash
git add services/data/fetchers.py services/data/indicators.py \
        services/data/tests/test_indicators.py
git commit -m "feat: expand data service to 250-bar fetch and 26-feature indicators"
```

---

## Task 3: Publish expanded snapshot

**Files:**
- Modify: `services/data/main.py`
- Modify: `services/data/tests/test_publisher.py`

- [ ] **Step 1: Write a failing test asserting new keys in the snapshot**

In `services/data/tests/test_publisher.py`, update the `_sample_snapshot()` helper and the required-fields test. Find and replace the existing `_sample_snapshot` function and `test_publish_snapshot_json_contains_all_required_fields`:

```python
def _sample_snapshot() -> dict:
    """Helper to generate a sample snapshot with all expanded keys."""
    return {
        "symbol": "AAPL",
        "timestamp": "2026-05-15T14:00:00+00:00",
        "price": 175.50,
        # Legacy keys
        "rsi": 52.3, "sma20": 172.1, "sma50": 168.5,
        "sma20_prev": 171.8, "sma20_prev2": 171.5,
        # ML feature keys
        "rsi_7": 50.1, "rsi_14": 52.3, "rsi_21": 53.0,
        "mom_5d": 0.01, "mom_10d": 0.02, "mom_20d": 0.03,
        "sma_10": 174.0, "sma_20": 172.1, "sma_50": 168.5, "sma_200": 160.0,
        "dist_sma10": 0.009, "dist_sma20": 0.02, "dist_sma50": 0.04, "dist_sma200": 0.097,
        "atr_14": 3.1, "bb_width": 0.06, "dist_bb_upper": 0.02, "dist_bb_lower": 0.04,
        "vol_zscore": 0.5, "vol_ratio": 1.1,
        "macd_line": 0.8, "macd_signal": 0.6, "macd_hist": 0.2,
        "dist_52w_high": 0.05, "dist_52w_low": 0.2,
        "day_of_week": 2, "volume": 1_500_000,
        "news": [{"headline": "Apple hits record", "datetime": 1715000000}],
        "macro": {"fed_funds_rate": 5.33, "cpi": 314.5},
    }


def test_publish_snapshot_json_contains_all_required_fields():
    """Verify that the JSON contains all legacy and ML feature fields."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        args, kwargs = mock_redis.xadd.call_args
        fields = args[1]
        parsed = json.loads(fields["data"])

        required_fields = [
            "symbol", "timestamp", "price",
            "rsi", "sma20", "sma50", "sma20_prev", "sma20_prev2",
            "rsi_7", "rsi_14", "rsi_21",
            "macd_hist", "atr_14", "vol_zscore", "vol_ratio",
            "dist_sma20", "dist_52w_high", "day_of_week", "volume",
            "news", "macro",
        ]
        for field in required_fields:
            assert field in parsed, f"Missing required field: {field}"
```

- [ ] **Step 2: Run to confirm the new key assertions fail**

```bash
cd services/data && python -m pytest tests/test_publisher.py::test_publish_snapshot_json_contains_all_required_fields -v
```

Expected: `FAILED` — keys like `rsi_7`, `macd_hist` are missing from the snapshot.

- [ ] **Step 3: Update snapshot assembly in `services/data/main.py`**

Find the snapshot dict in `_fetch_and_publish_price` and replace it with:

```python
        snapshot = {
            "symbol":    symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price":     price,
            **indicators,   # spreads all legacy + ML keys
            "news":      cached_news,
            "macro":     cached_macro,
        }
```

The `**indicators` spread works because `calculate_indicators()` now returns a flat dict containing all legacy keys (`rsi`, `sma20`, etc.) and all ML keys (`rsi_7`, `macd_hist`, etc.).

- [ ] **Step 4: Run all data service tests**

```bash
cd services/data && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/data/main.py services/data/tests/test_publisher.py
git commit -m "feat: publish expanded 26-feature snapshot from data service"
```

---

## Task 4: Expand research backtester snapshot

**Files:**
- Modify: `services/research/backtester.py`
- Modify: `services/research/tests/test_backtester.py`

- [ ] **Step 1: Write failing tests for ML keys in backtest snapshots**

Add to `services/research/tests/test_backtester.py` (after the existing `_make_bars` helper, update it to include `h`, `l`, and `t` fields — ATR needs highs/lows):

First, update `_make_bars`:

```python
def _make_bars(n: int, start_price: float = 100.0) -> list[dict]:
    """Generate n bars with a gentle uptrend. Need n >= 210 for full indicator set."""
    bars = []
    price = start_price
    for i in range(n):
        bars.append({
            "t": f"2024-01-{(i % 28) + 1:02d}",
            "o": round(price * 0.999, 4),
            "h": round(price * 1.010, 4),
            "l": round(price * 0.990, 4),
            "c": round(price, 4),
            "v": 1_000_000,
        })
        price = round(price * 1.001, 4)
    return bars
```

Then add these test methods inside `TestComputeIndicatorsSeries`:

```python
    def test_returns_something_for_210_bars(self):
        bars = _make_bars(211)
        result = compute_indicators_series(bars)
        self.assertGreater(len(result), 0)

    def test_snapshots_contain_ml_keys(self):
        bars = _make_bars(211)
        snapshots = compute_indicators_series(bars)
        self.assertGreater(len(snapshots), 0)
        snap = snapshots[-1]
        for key in ("rsi_7", "rsi_14", "rsi_21", "macd_hist", "atr_14",
                    "vol_zscore", "vol_ratio", "dist_sma20", "day_of_week"):
            self.assertIn(key, snap, f"Missing ML key: {key}")

    def test_legacy_keys_still_present(self):
        bars = _make_bars(211)
        snapshots = compute_indicators_series(bars)
        self.assertGreater(len(snapshots), 0)
        snap = snapshots[-1]
        for key in ("price", "rsi", "sma20", "sma50", "sma20_prev",
                    "sma20_prev2", "volume", "volume_avg"):
            self.assertIn(key, snap, f"Missing legacy key: {key}")
```

- [ ] **Step 2: Run to confirm the new tests fail**

```bash
cd services/research && python -m pytest tests/test_backtester.py -v -k "ml_keys or 210_bars or legacy_keys"
```

Expected: failures — snapshots don't contain ML keys yet.

- [ ] **Step 3: Rewrite `compute_indicators_series` in `services/research/backtester.py`**

Add `from typing import Optional` at the top and `import ta.volatility` alongside the existing ta imports. Then replace `compute_indicators_series`:

```python
_MIN_BARS = 210  # SMA200 needs 200 bars


def compute_indicators_series(bars: list[dict]) -> list[dict]:
    """
    Compute rolling indicators across all bars and return one snapshot per bar
    where core legacy indicators are valid. Each snapshot includes:
      _bar_idx  — index into bars list
      _open     — next bar's open (fill price for trades)

    Returns [] if fewer than _MIN_BARS bars.
    The last bar is always excluded (no next bar to fill at).
    All ML feature values that are NaN are included as None.
    """
    if len(bars) < _MIN_BARS:
        return []

    closes  = pd.Series([float(b["c"]) for b in bars])
    highs   = pd.Series([float(b["h"]) for b in bars])
    lows    = pd.Series([float(b["l"]) for b in bars])
    volumes = pd.Series([float(b["v"]) for b in bars])

    rsi_7_s  = ta.momentum.RSIIndicator(close=closes, window=7).rsi()
    rsi_14_s = ta.momentum.RSIIndicator(close=closes, window=14).rsi()
    rsi_21_s = ta.momentum.RSIIndicator(close=closes, window=21).rsi()

    sma_10_s  = ta.trend.SMAIndicator(close=closes, window=10).sma_indicator()
    sma_20_s  = ta.trend.SMAIndicator(close=closes, window=20).sma_indicator()
    sma_50_s  = ta.trend.SMAIndicator(close=closes, window=50).sma_indicator()
    sma_200_s = ta.trend.SMAIndicator(close=closes, window=200).sma_indicator()

    mom_5d_s  = closes.pct_change(5)
    mom_10d_s = closes.pct_change(10)
    mom_20d_s = closes.pct_change(20)

    atr_14_s   = ta.volatility.AverageTrueRange(
        high=highs, low=lows, close=closes, window=14
    ).average_true_range()
    bb_ind     = ta.volatility.BollingerBands(close=closes, window=20, window_dev=2)
    bb_upper_s = bb_ind.bollinger_hband()
    bb_lower_s = bb_ind.bollinger_lband()
    bb_mid_s   = bb_ind.bollinger_mavg()

    vol_mean_s = volumes.rolling(window=20).mean()
    vol_std_s  = volumes.rolling(window=20).std()
    vol_avg5_s = volumes.rolling(window=5).mean()

    macd_ind      = ta.trend.MACD(close=closes)
    macd_line_s   = macd_ind.macd()
    macd_signal_s = macd_ind.macd_signal()
    macd_hist_s   = macd_ind.macd_diff()

    high_252_s = closes.rolling(window=252, min_periods=50).max()
    low_252_s  = closes.rolling(window=252, min_periods=50).min()

    def _f(val) -> Optional[float]:
        return None if pd.isna(val) else float(val)

    snapshots = []
    for i in range(len(bars) - 1):
        # Skip until core legacy indicators are valid
        if (pd.isna(rsi_14_s.iloc[i]) or pd.isna(sma_20_s.iloc[i])
                or pd.isna(sma_50_s.iloc[i]) or i < 2
                or pd.isna(sma_20_s.iloc[i - 1]) or pd.isna(sma_20_s.iloc[i - 2])
                or pd.isna(vol_mean_s.iloc[i])):
            continue

        c    = closes.iloc[i]
        s10  = sma_10_s.iloc[i]
        s20  = sma_20_s.iloc[i]
        s50  = sma_50_s.iloc[i]
        s200 = sma_200_s.iloc[i]

        bb_up  = bb_upper_s.iloc[i]
        bb_lo  = bb_lower_s.iloc[i]
        bb_mid = bb_mid_s.iloc[i]
        bb_w   = (bb_up - bb_lo) if not (pd.isna(bb_up) or pd.isna(bb_lo)) else float("nan")

        vm  = vol_mean_s.iloc[i]
        vs  = vol_std_s.iloc[i]
        va5 = vol_avg5_s.iloc[i]
        v   = volumes.iloc[i]
        vol_z = float((v - vm) / vs) if (not pd.isna(vs) and vs != 0) else None
        vol_r = float(v / va5)       if (not pd.isna(va5) and va5 != 0) else None

        h252 = high_252_s.iloc[i]
        l252 = low_252_s.iloc[i]

        try:
            dow = min(pd.Timestamp(bars[i]["t"]).weekday(), 4)
        except Exception:
            dow = 0

        snapshots.append({
            # Legacy keys
            "price":       float(c),
            "rsi":         float(rsi_14_s.iloc[i]),
            "sma20":       float(s20),
            "sma50":       float(s50),
            "sma20_prev":  float(sma_20_s.iloc[i - 1]),
            "sma20_prev2": float(sma_20_s.iloc[i - 2]),
            "volume":      float(v),
            "volume_avg":  float(vm) if not pd.isna(vm) else None,
            # ML feature keys
            "rsi_7":        _f(rsi_7_s.iloc[i]),
            "rsi_14":       float(rsi_14_s.iloc[i]),
            "rsi_21":       _f(rsi_21_s.iloc[i]),
            "mom_5d":       _f(mom_5d_s.iloc[i]),
            "mom_10d":      _f(mom_10d_s.iloc[i]),
            "mom_20d":      _f(mom_20d_s.iloc[i]),
            "sma_10":       _f(s10),
            "sma_20":       _f(s20),
            "sma_50":       _f(s50),
            "sma_200":      _f(s200),
            "dist_sma10":   float((c - s10) / s10)   if not (pd.isna(s10)  or s10  == 0) else None,
            "dist_sma20":   float((c - s20) / s20)   if not (pd.isna(s20)  or s20  == 0) else None,
            "dist_sma50":   float((c - s50) / s50)   if not (pd.isna(s50)  or s50  == 0) else None,
            "dist_sma200":  float((c - s200) / s200) if not (pd.isna(s200) or s200 == 0) else None,
            "atr_14":       _f(atr_14_s.iloc[i]),
            "bb_width":     float(bb_w / bb_mid) if not (pd.isna(bb_w) or pd.isna(bb_mid) or bb_mid == 0) else None,
            "dist_bb_upper": float((bb_up - c) / c) if (not pd.isna(bb_up) and c != 0) else None,
            "dist_bb_lower": float((c - bb_lo) / c) if (not pd.isna(bb_lo) and c != 0) else None,
            "vol_zscore":   vol_z,
            "vol_ratio":    vol_r,
            "macd_line":    _f(macd_line_s.iloc[i]),
            "macd_signal":  _f(macd_signal_s.iloc[i]),
            "macd_hist":    _f(macd_hist_s.iloc[i]),
            "dist_52w_high": float((h252 - c) / c) if (not pd.isna(h252) and c != 0) else None,
            "dist_52w_low":  float((c - l252) / c) if (not pd.isna(l252) and c != 0) else None,
            "day_of_week":  dow,
            # Backtest-internal keys
            "_bar_idx": i,
            "_open":    float(bars[i + 1]["o"]),
        })
    return snapshots
```

- [ ] **Step 4: Run all research service tests**

```bash
cd services/research && python -m pytest tests/ -v
```

Expected: all tests pass. The existing `test_returns_empty_for_fewer_than_52_bars` still passes (51 < 210). BUY_CODE and HOLD_CODE tests still pass because they only use the `price` key.

- [ ] **Step 5: Commit**

```bash
git add services/research/backtester.py services/research/tests/test_backtester.py
git commit -m "feat: expand research backtester snapshot to 26-feature ML set"
```

---

## Task 5: Update codegen prompt and dry-run snapshots

**Files:**
- Modify: `services/ml/codegen.py`
- Modify: `services/ml/tests/test_codegen.py`

- [ ] **Step 1: Write a failing test that asserts the prompt lists ML keys**

Add to `services/ml/tests/test_codegen.py`:

```python
def test_build_prompt_contains_ml_snapshot_keys():
    pattern = _make_pattern()
    prompt = _build_prompt(pattern)
    for key in ("rsi_14", "macd_hist", "dist_sma20", "vol_zscore", "atr_14"):
        assert key in prompt, f"Prompt missing key: {key}"


def test_build_prompt_does_not_mention_volume_avg():
    """volume_avg is not a real snapshot key — it must not appear in the prompt."""
    pattern = _make_pattern()
    prompt = _build_prompt(pattern)
    assert "volume_avg" not in prompt
```

- [ ] **Step 2: Run to confirm the tests fail**

```bash
cd services/ml && python -m pytest tests/test_codegen.py::test_build_prompt_contains_ml_snapshot_keys tests/test_codegen.py::test_build_prompt_does_not_mention_volume_avg -v
```

Expected: both `FAILED`.

- [ ] **Step 3: Update `_DRY_RUN_SNAPSHOTS` and `_build_prompt` in `services/ml/codegen.py`**

Replace the `_DRY_RUN_SNAPSHOTS` constant and `_build_prompt` function:

```python
_DRY_RUN_SNAPSHOTS = [
    {
        "price": 150.0, "volume": 1_500_000,
        "rsi_7": 32.0, "rsi_14": 35.0, "rsi_21": 38.0,
        "mom_5d": -0.02, "mom_10d": -0.03, "mom_20d": -0.05,
        "sma_10": 148.0, "sma_20": 148.0, "sma_50": 145.0, "sma_200": 140.0,
        "dist_sma10": 0.014, "dist_sma20": 0.014, "dist_sma50": 0.034, "dist_sma200": 0.071,
        "atr_14": 3.2, "bb_width": 0.08, "dist_bb_upper": 0.04, "dist_bb_lower": 0.03,
        "vol_zscore": 1.2, "vol_ratio": 1.25,
        "macd_line": -0.5, "macd_signal": -0.3, "macd_hist": -0.2,
        "dist_52w_high": 0.15, "dist_52w_low": 0.05, "day_of_week": 1,
    },
    {
        "price": 200.0, "volume": 800_000,
        "rsi_7": 68.0, "rsi_14": 65.0, "rsi_21": 62.0,
        "mom_5d": 0.03, "mom_10d": 0.05, "mom_20d": 0.08,
        "sma_10": 197.0, "sma_20": 195.0, "sma_50": 190.0, "sma_200": 180.0,
        "dist_sma10": 0.015, "dist_sma20": 0.026, "dist_sma50": 0.053, "dist_sma200": 0.111,
        "atr_14": 4.5, "bb_width": 0.06, "dist_bb_upper": 0.01, "dist_bb_lower": 0.05,
        "vol_zscore": -0.5, "vol_ratio": 0.73,
        "macd_line": 1.2, "macd_signal": 0.9, "macd_hist": 0.3,
        "dist_52w_high": 0.02, "dist_52w_low": 0.25, "day_of_week": 3,
    },
    {
        "price": 100.0, "volume": 1_000_000,
        "rsi_7": 52.0, "rsi_14": 50.0, "rsi_21": 49.0,
        "mom_5d": 0.0, "mom_10d": 0.01, "mom_20d": 0.02,
        "sma_10": 101.0, "sma_20": 101.0, "sma_50": 99.0, "sma_200": 95.0,
        "dist_sma10": -0.01, "dist_sma20": -0.01, "dist_sma50": 0.01, "dist_sma200": 0.053,
        "atr_14": 2.0, "bb_width": 0.05, "dist_bb_upper": 0.03, "dist_bb_lower": 0.02,
        "vol_zscore": 0.0, "vol_ratio": 1.0,
        "macd_line": 0.1, "macd_signal": 0.05, "macd_hist": 0.05,
        "dist_52w_high": 0.08, "dist_52w_low": 0.12, "day_of_week": 2,
    },
]


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
  price, volume,
  rsi_7, rsi_14, rsi_21,
  mom_5d, mom_10d, mom_20d,
  sma_10, sma_20, sma_50, sma_200,
  dist_sma10, dist_sma20, dist_sma50, dist_sma200,
  atr_14, bb_width, dist_bb_upper, dist_bb_lower,
  vol_zscore, vol_ratio,
  macd_line, macd_signal, macd_hist,
  dist_52w_high, dist_52w_low, day_of_week

Note: sma_10/sma_20/sma_50/sma_200 are raw price values. Prefer dist_sma* variants (normalised % distance) for cross-symbol strategies.
Note: snapshot values may be None if the indicator could not be computed — guard against this where needed.

Return format — return a dict with exactly these keys:
  {{"decision": "buy" | "sell" | "hold", "confidence": 0.0–1.0, "reasoning": "short explanation"}}

Rules:
- No imports
- No external calls
- No global state
- Handle None values gracefully (use `or 0` or guard with `if x is not None`)
- Use only the snapshot keys listed above

Output ONLY the Python function, wrapped in ```python ... ``` fences. No explanation."""
```

- [ ] **Step 4: Fix `test_validate_code_accepts_code_with_common_builtins`**

In `services/ml/tests/test_codegen.py`, update the test that uses the removed `volume_avg` key. Find the test and replace `volume_avg` references with `vol_ratio`:

```python
def test_validate_code_accepts_code_with_common_builtins():
    """Code using min/max/abs/round/isinstance should not be rejected."""
    code_with_builtins = '''
def generate_signal(snapshot):
    price = float(snapshot["price"])
    rsi = snapshot["rsi_14"]
    vol_ratio = snapshot.get("vol_ratio") or 1.0
    if rsi < 40 and vol_ratio > 1.2:
        conf = min(0.9, abs(rsi - 50) / 50)
        return {"decision": "buy", "confidence": conf, "reasoning": f"RSI={rsi:.1f}"}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "no signal"}
'''
    errors = _validate_code(code_with_builtins)
    assert errors == [], f"Unexpected errors: {errors}"
```

- [ ] **Step 5: Run all ML tests**

```bash
cd services/ml && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add services/ml/codegen.py services/ml/tests/test_codegen.py
git commit -m "feat: update codegen prompt to 26-feature snapshot keys"
```

---

## Task 6: Add replay gate

**Files:**
- Modify: `services/ml/discoverer.py`
- Create: `services/ml/validator.py`
- Create: `services/ml/tests/test_validator.py`
- Modify: `services/ml/pipeline.py`
- Modify: `config.toml`
- Modify: `services/ml/tests/test_discoverer.py`

- [ ] **Step 1: Write failing tests for the replay gate**

Create `services/ml/tests/test_validator.py`:

```python
"""Unit tests for validator.py — the pre-backtest replay gate."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

import pytest
from validator import validate_against_pattern


def _make_rows(n: int = 50, rsi_14: float = 38.0) -> list[dict]:
    """Build synthetic feature rows that match the 26-key snapshot schema."""
    return [
        {
            "price": 150.0, "volume": 1_000_000,
            "rsi_7": 35.0, "rsi_14": rsi_14, "rsi_21": 40.0,
            "mom_5d": -0.02, "mom_10d": -0.03, "mom_20d": -0.05,
            "sma_10": 148.0, "sma_20": 148.0, "sma_50": 145.0, "sma_200": 140.0,
            "dist_sma10": 0.014, "dist_sma20": 0.014, "dist_sma50": 0.034, "dist_sma200": 0.071,
            "atr_14": 3.2, "bb_width": 0.08, "dist_bb_upper": 0.04, "dist_bb_lower": 0.03,
            "vol_zscore": 1.2, "vol_ratio": 1.25,
            "macd_line": -0.5, "macd_signal": -0.3, "macd_hist": -0.2,
            "dist_52w_high": 0.15, "dist_52w_low": 0.05, "day_of_week": 1,
        }
        for _ in range(n)
    ]


_CFG = {"min_replay_signal_rate": 0.20, "min_replay_buy_rate": 0.40}

_ALWAYS_BUY = '''
def generate_signal(snapshot):
    return {"decision": "buy", "confidence": 0.7, "reasoning": "always buy"}
'''

_ALWAYS_HOLD = '''
def generate_signal(snapshot):
    return {"decision": "hold", "confidence": 0.5, "reasoning": "always hold"}
'''

_ONLY_SELL = '''
def generate_signal(snapshot):
    return {"decision": "sell", "confidence": 0.6, "reasoning": "always sell"}
'''

_RSI_BUY = '''
def generate_signal(snapshot):
    rsi = snapshot.get("rsi_14") or 50
    if rsi < 40:
        return {"decision": "buy", "confidence": 0.7, "reasoning": "RSI oversold"}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "no signal"}
'''


def test_always_buy_passes():
    assert validate_against_pattern(_ALWAYS_BUY, _make_rows(50), _CFG) is True


def test_always_hold_fails_signal_rate():
    assert validate_against_pattern(_ALWAYS_HOLD, _make_rows(50), _CFG) is False


def test_only_sell_fails_buy_rate():
    assert validate_against_pattern(_ONLY_SELL, _make_rows(50), _CFG) is False


def test_rsi_strategy_passes_when_rows_trigger():
    """Strategy that buys on rsi_14 < 40 passes when all rows have rsi_14=38."""
    assert validate_against_pattern(_RSI_BUY, _make_rows(50, rsi_14=38.0), _CFG) is True


def test_rsi_strategy_fails_when_rows_never_trigger():
    """Strategy that buys on rsi_14 < 40 fails when all rows have rsi_14=70."""
    assert validate_against_pattern(_RSI_BUY, _make_rows(50, rsi_14=70.0), _CFG) is False


def test_empty_rows_fails():
    assert validate_against_pattern(_ALWAYS_BUY, [], _CFG) is False


def test_invalid_code_fails():
    assert validate_against_pattern("not valid python!!!", _make_rows(50), _CFG) is False


def test_custom_thresholds_respected():
    """A strict config (signal_rate=1.0) rejects a strategy that sometimes holds."""
    strict_cfg = {"min_replay_signal_rate": 1.0, "min_replay_buy_rate": 0.40}
    assert validate_against_pattern(_RSI_BUY, _make_rows(50, rsi_14=38.0), strict_cfg) is True
    assert validate_against_pattern(_RSI_BUY, _make_rows(50, rsi_14=50.0), strict_cfg) is False
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd services/ml && python -m pytest tests/test_validator.py -v
```

Expected: `ModuleNotFoundError: No module named 'validator'`

- [ ] **Step 3: Create `services/ml/validator.py`**

```python
"""services/ml/validator.py — Pre-backtest replay gate.

Replays generated strategy code against the historical rows that defined the
pattern. Rejects code that fires on fewer than min_replay_signal_rate of rows
or has fewer than min_replay_buy_rate buy signals among its fires.
"""
import ast
import logging

log = logging.getLogger("ml.validator")

_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "float": float, "int": int, "isinstance": isinstance, "len": len,
    "list": list, "max": max, "min": min, "range": range,
    "round": round, "str": str, "tuple": tuple, "zip": zip,
}


def validate_against_pattern(code: str, pattern_rows: list[dict], cfg: dict) -> bool:
    """Replay code against pattern rows. Returns True if signal and buy rates pass.

    Args:
        code:         Generated strategy code string (from codegen).
        pattern_rows: Historical feature rows that defined the pattern.
        cfg:          ML config dict with optional keys:
                        min_replay_signal_rate (default 0.20)
                        min_replay_buy_rate    (default 0.40)
    """
    min_signal_rate = cfg.get("min_replay_signal_rate", 0.20)
    min_buy_rate    = cfg.get("min_replay_buy_rate", 0.40)

    if not pattern_rows:
        log.warning("Replay gate: no rows to replay against — rejecting")
        return False

    try:
        tree = ast.parse(code)
        namespace: dict = {"__builtins__": _SAFE_BUILTINS}
        exec(compile(tree, "<string>", "exec"), namespace)  # noqa: S102
        fn = namespace.get("generate_signal")
        if not callable(fn):
            log.error("Replay gate: generate_signal not callable after exec")
            return False
    except Exception as exc:
        log.error("Replay gate: code exec failed: %s", exc)
        return False

    fires = 0
    buy_fires = 0

    for row in pattern_rows:
        try:
            result = fn(row)
            decision = result.get("decision") if isinstance(result, dict) else None
            if decision in ("buy", "sell"):
                fires += 1
                if decision == "buy":
                    buy_fires += 1
        except Exception:
            pass  # individual row failures do not fail the gate

    n = len(pattern_rows)
    signal_rate = fires / n
    buy_rate    = buy_fires / fires if fires > 0 else 0.0

    passed = signal_rate >= min_signal_rate and buy_rate >= min_buy_rate
    log.info(
        "Replay gate: signal_rate=%.2f (min=%.2f) buy_rate=%.2f (min=%.2f) -> %s",
        signal_rate, min_signal_rate, buy_rate, min_buy_rate,
        "PASS" if passed else "FAIL",
    )
    return passed
```

- [ ] **Step 4: Run validator tests to confirm they pass**

```bash
cd services/ml && python -m pytest tests/test_validator.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Add `rows` field to `CandidatePattern` in `discoverer.py`**

At the top of `services/ml/discoverer.py`, update the import:

```python
from dataclasses import dataclass, field
```

Then update the `CandidatePattern` dataclass:

```python
@dataclass
class CandidatePattern:
    pattern_type:           str
    rule_description:       str
    example_count:          int
    avg_forward_return_pct: float
    win_rate_pct:           float
    sharpe:                 float
    symbol:                 Optional[str] = None
    rows:                   list[dict] = field(default_factory=list)
```

- [ ] **Step 6: Capture per-leaf rows in `_profile_leaf`**

Replace `_profile_leaf` in `services/ml/discoverer.py`:

```python
def _profile_leaf(rows: list[dict], tree: DecisionTreeClassifier,
                  X: np.ndarray) -> list[tuple[str, list[float], list[dict]]]:
    """For each leaf, collect the forward returns, rule path, and source rows."""
    tree_ = tree.tree_
    leaf_ids = tree.apply(X)

    leaf_returns: dict[int, list[float]] = {}
    leaf_rows_map: dict[int, list[dict]] = {}

    def collect_leaves(node: int) -> None:
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            leaf_returns[node] = []
            leaf_rows_map[node] = []
        else:
            collect_leaves(tree_.children_left[node])
            collect_leaves(tree_.children_right[node])

    collect_leaves(0)

    for i, leaf_id in enumerate(leaf_ids):
        if leaf_id in leaf_returns:
            leaf_returns[leaf_id].append(rows[i]["fwd_return_10"])
            leaf_rows_map[leaf_id].append(rows[i])

    rule_returns: list[tuple[str, list[float], list[dict]]] = []

    def recurse_with_rule(node: int, conditions: list[str]) -> None:
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            if node in leaf_returns:
                rule = " AND ".join(conditions) if conditions else "all bars"
                rule_returns.append((rule, leaf_returns[node], leaf_rows_map.get(node, [])))
        else:
            fname = FEATURE_NAMES[tree_.feature[node]]
            threshold = tree_.threshold[node]
            recurse_with_rule(
                tree_.children_left[node],
                conditions + [f"{fname} <= {threshold:.4f}"],
            )
            recurse_with_rule(
                tree_.children_right[node],
                conditions + [f"{fname} > {threshold:.4f}"],
            )

    recurse_with_rule(0, [])
    return rule_returns
```

- [ ] **Step 7: Pass rows into DT candidates**

In `_extract_dt_patterns`, update the loop to unpack the third element and pass `rows` to `CandidatePattern`:

```python
    rule_returns = _profile_leaf(rows, clf, X)
    candidates = []

    for rule, returns, pattern_rows in rule_returns:
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
                rows=pattern_rows,
            ))
```

- [ ] **Step 8: Pass rows into cluster candidates**

In `_extract_cluster_patterns`, replace the per-cluster returns extraction:

```python
    candidates = []
    for cluster_id in range(k):
        mask         = labels == cluster_id
        cluster_rows = [rows[i] for i in range(len(rows)) if mask[i]]
        n            = len(cluster_rows)
        returns      = [r["fwd_return_10"] for r in cluster_rows]

        if not returns:
            continue

        avg_ret_pct = float(np.mean(returns)) * 100
        win_rate    = float(np.mean([r > 0 for r in returns])) * 100
        sh          = _sharpe(returns)

        if (n >= _KMEANS_MIN_EXAMPLES
                and avg_ret_pct >= cfg["min_forward_return_pct"]
                and win_rate >= cfg["min_win_rate_pct"]):
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
                symbol=None,
                rows=cluster_rows,
            ))
```

- [ ] **Step 9: Write a failing test that patterns have rows**

Add to `services/ml/tests/test_discoverer.py`:

```python
def test_dt_patterns_have_rows():
    """Each discovered DT pattern must have at least min_examples rows."""
    from discoverer import discover_patterns, CandidatePattern
    from features import FEATURE_NAMES
    import numpy as np
    from datetime import date, timedelta

    rng = np.random.default_rng(42)
    today = date.today()
    rows = []
    for i in range(200):
        row = {f: float(rng.uniform(0, 1)) for f in FEATURE_NAMES}
        row["bar_date"] = today - timedelta(days=200 - i)
        row["fwd_return_10"] = float(rng.uniform(-0.05, 0.10))
        rows.append(row)

    features = {"AAPL": rows}
    cfg = {
        "lookback_days_momentum": 365,
        "lookback_days_regime": 1825,
        "max_strategies_per_run": 5,
        "min_examples": 10,
        "min_forward_return_pct": 0.0,
        "min_win_rate_pct": 0.0,
    }
    patterns = discover_patterns(features, cfg)
    for p in patterns:
        assert len(p.rows) >= cfg["min_examples"], (
            f"Pattern '{p.rule_description[:40]}' has {len(p.rows)} rows, expected >= {cfg['min_examples']}"
        )
```

- [ ] **Step 10: Run discoverer tests to confirm the new test fails**

```bash
cd services/ml && python -m pytest tests/test_discoverer.py::test_dt_patterns_have_rows -v
```

Expected: `FAILED` — `rows` field doesn't exist yet (before the changes in steps 5–8 above were applied).

> Note: If you've already applied the changes in steps 5–8, this test may pass immediately. That's fine — continue to step 11.

- [ ] **Step 11: Run all discoverer tests**

```bash
cd services/ml && python -m pytest tests/test_discoverer.py -v
```

Expected: all tests pass.

- [ ] **Step 12: Wire the replay gate into `pipeline.py`**

Add `from validator import validate_against_pattern` to the imports in `services/ml/pipeline.py`.

Then in `_run_phases()`, find the codegen loop and add the gate call after `generate_strategy_code`:

```python
        for pattern in patterns:
            code = generate_strategy_code(pattern, client=anthropic_client)
            if code is None:
                log.warning("Codegen failed for pattern: %.60s", pattern.rule_description)
                continue

            if not validate_against_pattern(code, pattern.rows, ml_cfg):
                log.warning(
                    "Replay gate rejected strategy for pattern: %.60s",
                    pattern.rule_description,
                )
                continue

            h = code_hash(code)
            strategy_name = (
                f"ML-{pattern.pattern_type[:2].upper()}-{pattern.symbol or 'XSYM'}-{h[:6]}"
            )
            # ... rest of the save/backtest logic unchanged ...
```

- [ ] **Step 13: Add replay threshold keys to `config.toml`**

Under `[ml]`, append:

```toml
min_replay_signal_rate = 0.20
min_replay_buy_rate    = 0.40
```

- [ ] **Step 14: Run the full ML test suite**

```bash
cd services/ml && python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 15: Commit**

```bash
git add services/ml/discoverer.py services/ml/validator.py \
        services/ml/pipeline.py services/ml/tests/test_validator.py \
        services/ml/tests/test_discoverer.py config.toml
git commit -m "feat: add replay gate and capture per-pattern rows in discoverer"
```

---

## Final verification

- [ ] **Run full test suite across all affected services**

```bash
pytest services/data/tests/ services/research/tests/ services/ml/tests/ -v
```

Expected: all tests pass. Note any failures and fix before proceeding.

- [ ] **Rebuild and restart ML + data + research containers**

```bash
docker-compose up -d --build ml data research
```

- [ ] **Watch the ML pipeline run and confirm symbols are processed**

```bash
docker logs -f alphadivision-ml-1 2>&1 | grep -E "Phase|patterns|strategies|promoted|ERROR"
```

Expected output pattern:
```
Phase 1 complete: 26 symbols with data
Phase 2 complete: 26 symbols with features
Phase 3 complete: N candidate patterns
Phase 4 complete: N strategies generated
Replay gate: signal_rate=X.XX ... -> PASS   (at least some)
Phase 5 complete: N/M strategies promoted to candidate
```
