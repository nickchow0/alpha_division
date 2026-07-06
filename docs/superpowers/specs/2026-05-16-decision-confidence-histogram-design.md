# Decision Confidence Histogram — Design Spec

## Goal

Add a dedicated `/analysis` page to the dashboard that visualises AI decision confidence distributions and their relationship to trade outcomes, enabling evaluation of whether the 0.65 confidence threshold is well-calibrated over time.

## Architecture

New page follows the exact pattern of `/charts`:

- **`services/dashboard/queries.py`** — 4 new SQL functions
- **`services/dashboard/main.py`** — 2 new routes (`/analysis`, `/api/analysis`)
- **`services/dashboard/templates/analysis.html`** — extends `base.html`, Chart.js charts
- **`services/dashboard/templates/base.html`** — add "Analysis" nav link between Decisions and Watchlist

No new config keys. No new dependencies. No new Docker services.

## Bucketing

All three charts use **20 buckets of 5%** (0–5%, 5–10%, …, 95–100%).

SQL: `WIDTH_BUCKET(LEAST(confidence::numeric, 0.9999), 0, 1, 20)` returns integers 1–20. The `LEAST()` guard handles the edge case where `confidence = 1.0` exactly — `WIDTH_BUCKET` would return 21 for that value, which is out of range.

The 0.65 threshold falls exactly on the boundary between buckets 13 and 14:
- Buckets 1–13: confidence < 0.65 (below threshold) — styled gray (`#8b949e`)
- Buckets 14–20: confidence ≥ 0.65 (above threshold) — styled blue (`#58a6ff`)

## Queries

### `get_confidence_histogram(days: int | None) -> list`

Returns 20 rows (one per bucket), including zero-count buckets via `generate_series` left join. Each row: `bucket` (1–20), `label` (e.g. `"60-65%"`), `count`.

```sql
WITH buckets AS (
    SELECT
        WIDTH_BUCKET(confidence::numeric, 0, 1, 20) AS bucket,
        COUNT(*) AS count
    FROM decisions
    WHERE confidence IS NOT NULL
      AND (<days filter if days is not None>)
    GROUP BY bucket
)
SELECT
    s.n                                          AS bucket,
    (((s.n - 1) * 5)::text || '-' || (s.n * 5)::text || '%') AS label,
    COALESCE(b.count, 0)                         AS count
FROM generate_series(1, 20) s(n)
LEFT JOIN buckets b ON b.bucket = s.n
ORDER BY s.n
```

### `get_acted_on_rate_by_band(days: int | None) -> list`

Same bucketing. Each row: `bucket`, `label`, `total`, `acted`, `acted_pct` (float, 0.0 when total = 0).

### `get_win_rate_by_band(days: int | None) -> list`

Joins `decisions → signals → trades` using the LATERAL join pattern from `get_trade_stats()`. Only includes closed trades (filled sell matched to most recent filled buy per symbol).

Each row: `bucket`, `label`, `sample_size`, `wins`, `win_rate_pct`.

Returns only buckets where `sample_size > 0`. Returns an empty list when no closed trades exist.

### `get_analysis_stats(days: int | None) -> dict`

Single-row aggregate. Returns:

```python
{
    "total_decisions": int,
    "median_confidence": float,   # PERCENTILE_CONT(0.5), 0.0 if no data
    "pct_above_threshold": float, # % with confidence >= 0.65
    "pct_acted_on": float,
    "haiku_count": int,
    "sonnet_count": int,
}
```

Uses `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY confidence::numeric)`.

## Routes

### `GET /analysis`

Server-renders `analysis.html` with `days=30` default. Passes all four datasets as template variables. Same pattern as `GET /charts`.

### `GET /api/analysis?days=30|90|all`

JSON endpoint for time window switching. Validates `days` param: accepts `"30"`, `"90"`, `"all"` — anything else silently falls back to `30`. `"all"` maps to `None` (omits date filter in queries).

Returns:
```json
{
  "stats": { ... },
  "histogram": [ {"bucket": 1, "label": "0-5%", "count": 0}, ... ],
  "acted_on_rate": [ ... ],
  "win_rate": [ ... ]
}
```

## Page Layout

Top to bottom:

### Time Selector
Three toggle buttons: **30 days** (default active) / **90 days** / **All time**. Clicking fires `GET /api/analysis?days=N` and re-renders all charts and stats without a page reload.

### Stat Row (6 cards)
`Total Decisions` · `Median Confidence` · `Above Threshold` · `Acted On` · `Haiku` · `Sonnet`

### Chart 1: Confidence Distribution
- Type: vertical bar chart (Chart.js `bar`)
- X: 20 bucket labels
- Y: decision count
- Colors: gray for buckets 1–13, blue for 14–20
- Empty state: "No decisions yet."

### Chart 2: Acted-on Rate by Band
- Type: bar chart
- X: same 20 buckets
- Y: acted-on % (0–100)
- Colors: same gray/blue threshold split
- Buckets with zero decisions shown as 0%
- Empty state: "No decisions yet."

### Chart 3: Win Rate by Confidence Band
- Type: bar chart
- X: only buckets with closed trade data
- Y: win rate % (0–100)
- Colors: green (`#3fb950`) if win rate ≥ 50%, red (`#f85149`) below
- Empty state: "No closed trade data yet — check back after the first full trade cycle."

All three charts auto-refresh when the time window is changed.

## Error Handling

- All query functions return safe empty defaults (empty list or zeroed dict) when the DB has no data.
- `get_win_rate_by_band` returns `[]` when no closed trades — template shows empty state message.
- `/api/analysis` validates `days` and falls back to 30 on invalid input — no 500.
- The page is read-only; upstream failures (circuit breaker, Alpaca API) have no effect on it.

## Testing

**Unit tests (`services/dashboard/tests/test_queries.py`):**
- `TestGetAnalysisStats` — zeros when no data, correct keys, median is float
- `TestGetConfidenceHistogram` — always 20 rows returned, zero-count buckets included, labels formatted correctly
- `TestGetActedOnRateByBand` — acted_pct is 0.0 when no decisions in bucket
- `TestGetWinRateByBand` — empty list when no closed trades, only non-zero-sample buckets returned

**Unit tests (`services/dashboard/tests/test_main.py`):**
- `test_analysis_returns_200`
- `test_api_analysis_returns_200`
- `test_api_analysis_invalid_days_falls_back_to_30`

No new integration tests — the LATERAL join pattern is already covered by `get_trade_stats` integration tests.

## What Happens When There's No Data

The page is designed to be useful from day one with zero trades:
- Histogram and acted-on charts show all 20 bars at zero with "No decisions yet."
- Win rate chart shows the empty state message
- Stats show all zeros / dashes
- No errors, no broken charts
