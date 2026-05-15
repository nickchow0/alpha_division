from typing import Optional

import pandas as pd
import ta.momentum
import ta.trend


def calculate_indicators(bars: list[dict]) -> Optional[dict]:
    """
    Calculate RSI(14), SMA(20), SMA(50), and the two previous SMA20 values
    from a list of bar dicts (each with at least a 'c' key for close price).

    Returns None if there are fewer than 50 bars (not enough for SMA50).
    Returns a dict with keys: rsi, sma20, sma50, sma20_prev, sma20_prev2.
    All values are Python floats.
    """
    if len(bars) < 50:
        return None

    closes = pd.Series([float(b["c"]) for b in bars])

    rsi_series = ta.momentum.RSIIndicator(close=closes, window=14).rsi()
    sma20_series = ta.trend.SMAIndicator(close=closes, window=20).sma_indicator()
    sma50_series = ta.trend.SMAIndicator(close=closes, window=50).sma_indicator()

    # Drop NaN values to get the last computed values
    rsi_vals = rsi_series.dropna()
    sma20_vals = sma20_series.dropna()
    sma50_vals = sma50_series.dropna()

    # Need at least 3 SMA20 values for prev and prev2
    if len(rsi_vals) == 0 or len(sma20_vals) < 3 or len(sma50_vals) == 0:
        return None

    return {
        "rsi": float(rsi_vals.iloc[-1]),
        "sma20": float(sma20_vals.iloc[-1]),
        "sma50": float(sma50_vals.iloc[-1]),
        "sma20_prev": float(sma20_vals.iloc[-2]),
        "sma20_prev2": float(sma20_vals.iloc[-3]),
    }
