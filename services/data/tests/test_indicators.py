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
