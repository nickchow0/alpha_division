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
            "day_of_week":    min(dates[i].weekday(), 4) if isinstance(dates[i], date) else 0,
            # Label
            "fwd_return_10":  float((closes.iloc[i + 10] - c) / c),
        }

        # Drop rows with any NaN in the 26 indicator features
        if any(
            pd.isna(row[k])
            for k in FEATURE_NAMES
        ):
            continue

        rows.append(row)

    log.debug("Feature computation: %d valid rows from %d bars", len(rows), len(bars))
    return rows
