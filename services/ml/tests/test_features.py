"""Unit tests for features.py — indicator computation."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from datetime import date, timedelta
import numpy as np
import pytest

from features import compute_features, FEATURE_NAMES, _MIN_BARS


def _make_bars(n: int, base_price: float = 100.0) -> list[dict]:
    """Generate synthetic OHLCV bars with realistic values."""
    bars = []
    price = base_price
    base_date = date(2020, 1, 2)
    for i in range(n):
        # Simple random walk
        price *= 1.0 + (0.001 * ((i % 7) - 3))
        bars.append({
            "date":   base_date + timedelta(days=i + (i // 5) * 2),  # skip weekends roughly
            "open":   round(price * 0.999, 4),
            "high":   round(price * 1.005, 4),
            "low":    round(price * 0.995, 4),
            "close":  round(price, 4),
            "volume": 1_000_000 + i * 5000,
        })
    return bars


def test_feature_names_count():
    assert len(FEATURE_NAMES) == 26


def test_compute_features_returns_empty_for_too_few_bars():
    bars = _make_bars(_MIN_BARS - 1)
    result = compute_features(bars)
    assert result == []


def test_compute_features_returns_rows_for_enough_bars():
    bars = _make_bars(_MIN_BARS + 50)
    result = compute_features(bars)
    assert len(result) > 0


def test_each_row_has_all_feature_names():
    bars = _make_bars(_MIN_BARS + 20)
    result = compute_features(bars)
    for row in result:
        for name in FEATURE_NAMES:
            assert name in row, f"Missing feature: {name}"


def test_no_nan_in_features():
    bars = _make_bars(_MIN_BARS + 50)
    result = compute_features(bars)
    for row in result:
        for name in FEATURE_NAMES:
            val = row[name]
            assert not (isinstance(val, float) and np.isnan(val)), \
                f"NaN in feature {name}"


def test_forward_return_present_and_finite():
    bars = _make_bars(_MIN_BARS + 50)
    result = compute_features(bars)
    for row in result:
        assert "fwd_return_10" in row
        assert isinstance(row["fwd_return_10"], float)
        assert not np.isnan(row["fwd_return_10"])


def test_day_of_week_in_range():
    bars = _make_bars(_MIN_BARS + 20)
    result = compute_features(bars)
    for row in result:
        assert 0 <= row["day_of_week"] <= 4


def test_rsi_in_range():
    bars = _make_bars(_MIN_BARS + 30)
    result = compute_features(bars)
    for row in result:
        for key in ("rsi_7", "rsi_14", "rsi_21"):
            assert 0 <= row[key] <= 100, f"{key} out of range: {row[key]}"


def test_vol_zscore_reasonable():
    """Volume z-score should be within [-5, 5] for synthetic data."""
    bars = _make_bars(_MIN_BARS + 50)
    result = compute_features(bars)
    for row in result:
        assert -10 < row["vol_zscore"] < 10
