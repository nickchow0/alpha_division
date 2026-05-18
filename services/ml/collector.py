"""services/ml/collector.py — Phase 1: Fetch and cache OHLCV bars.

Fetches daily OHLCV bars via yfinance for a list of symbols. Bars are cached
in the ml_bars Postgres table so only new bars are fetched on subsequent runs.
Uses ThreadPoolExecutor for parallel fetching.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

import pandas as pd
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
