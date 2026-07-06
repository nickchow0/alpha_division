"""services/ml/collector.py — Phase 1: Fetch and cache OHLCV bars.

Fetches daily OHLCV bars via Alpaca historical data API for a list of symbols.
Bars are cached in the ml_bars Postgres table so only new bars are fetched on
subsequent runs. Uses ThreadPoolExecutor for parallel fetching.
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import alpaca_trade_api as tradeapi
import pandas as pd

from queries import get_cached_bars, save_bars

log = logging.getLogger("ml.collector")

_MAX_WORKERS = 8  # Alpaca allows 200 req/min


def _alpaca_client() -> tradeapi.REST:
    return tradeapi.REST(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
        os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
    )


def _fetch_alpaca(symbol: str, start: date, end: date) -> list[dict]:
    """Fetch daily OHLCV bars from Alpaca for the given date range.

    Returns a list of dicts with keys: date, open, high, low, close, volume.
    Returns [] on error or if no bars are returned.
    """
    try:
        api = _alpaca_client()
        resp = api.get_bars(
            symbol,
            "1Day",
            start=start.isoformat(),
            end=end.isoformat(),
            limit=10000,
        )
        df = resp.df
    except Exception as exc:
        log.warning("%s: Alpaca fetch failed: %s", symbol, exc)
        return []

    if df.empty:
        return []

    bars = []
    for ts, row in df.iterrows():
        bar_date = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
        bars.append({
            "date":   bar_date,
            "open":   float(row["open"]),
            "high":   float(row["high"]),
            "low":    float(row["low"]),
            "close":  float(row["close"]),
            "volume": int(row["volume"]),
        })
    return bars


def _collect_symbol(symbol: str, lookback_days: int) -> list[dict]:
    """Fetch and cache bars for a single symbol.

    1. Loads cached bars from ml_bars.
    2. If cache is fully up-to-date (latest bar is yesterday or today), returns cache.
    3. Otherwise fetches the gap from Alpaca and saves new bars to ml_bars.
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
    new_bars = _fetch_alpaca(symbol, fetch_start, today + timedelta(days=1))

    if new_bars:
        save_bars(symbol, new_bars)
        log.info("%s: saved %d new bars", symbol, len(new_bars))
    else:
        log.warning("%s: no bars returned from Alpaca", symbol)

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
