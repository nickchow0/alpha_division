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
    rsi_valid   = rsi_14_s.dropna()
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
