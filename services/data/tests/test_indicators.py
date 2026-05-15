import pytest
from indicators import calculate_indicators


def _make_bars(prices: list[float]) -> list[dict]:
    """Build a minimal bars list from a list of close prices."""
    return [
        {"t": f"2026-01-{i+1:02d}", "o": p, "h": p, "l": p, "c": p, "v": 1_000_000}
        for i, p in enumerate(prices)
    ]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_returns_none_when_fewer_than_50_bars():
    bars = _make_bars([100.0] * 49)
    result = calculate_indicators(bars)
    assert result is None


def test_returns_none_when_zero_bars():
    result = calculate_indicators([])
    assert result is None


def test_returns_none_when_exactly_49_bars():
    bars = _make_bars([100.0] * 49)
    result = calculate_indicators(bars)
    assert result is None


def test_returns_dict_when_60_bars():
    bars = _make_bars([float(100 + i) for i in range(60)])
    result = calculate_indicators(bars)
    assert result is not None
    assert isinstance(result, dict)


def test_returns_dict_when_exactly_50_bars():
    bars = _make_bars([100.0] * 50)
    result = calculate_indicators(bars)
    assert result is not None


# ---------------------------------------------------------------------------
# Key presence
# ---------------------------------------------------------------------------

def test_result_has_all_required_keys():
    bars = _make_bars([float(100 + i) for i in range(60)])
    result = calculate_indicators(bars)
    assert result is not None
    for key in ("rsi", "sma20", "sma50", "sma20_prev", "sma20_prev2"):
        assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Value correctness
# ---------------------------------------------------------------------------

def test_rsi_is_in_valid_range():
    bars = _make_bars([float(100 + i) for i in range(60)])
    result = calculate_indicators(bars)
    assert result is not None
    assert 0.0 <= result["rsi"] <= 100.0


def test_sma50_equals_price_when_constant():
    # When all prices are the same, SMA50 must equal that price.
    bars = _make_bars([150.0] * 60)
    result = calculate_indicators(bars)
    assert result is not None
    assert abs(result["sma50"] - 150.0) < 0.01


def test_sma20_equals_price_when_constant():
    bars = _make_bars([200.0] * 60)
    result = calculate_indicators(bars)
    assert result is not None
    assert abs(result["sma20"] - 200.0) < 0.01


def test_sma20_prev_differs_from_sma20_on_trending_prices():
    # Rising price series: each SMA20 value will be slightly higher than the previous one.
    bars = _make_bars([float(i) for i in range(1, 61)])
    result = calculate_indicators(bars)
    assert result is not None
    # sma20_prev is the second-to-last value; it must be less than sma20 on a rising series.
    assert result["sma20_prev"] < result["sma20"]


def test_sma20_prev2_less_than_sma20_prev_on_rising_prices():
    bars = _make_bars([float(i) for i in range(1, 61)])
    result = calculate_indicators(bars)
    assert result is not None
    assert result["sma20_prev2"] < result["sma20_prev"]


def test_values_are_floats():
    bars = _make_bars([float(100 + i) for i in range(60)])
    result = calculate_indicators(bars)
    assert result is not None
    for key in ("rsi", "sma20", "sma50", "sma20_prev", "sma20_prev2"):
        assert isinstance(result[key], float), f"{key} should be float, got {type(result[key])}"
