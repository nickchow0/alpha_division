import pytest
from filters import passes_technical_filter, passes_sell_filter


def _snapshot(**overrides) -> dict:
    """Build a snapshot that passes all three filter rules by default."""
    base = {
        "symbol": "AAPL",
        "price": 175.0,
        "rsi": 52.0,
        "sma20": 170.0,
        "sma50": 160.0,
        "sma20_prev": 169.0,
        "sma20_prev2": 168.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# RSI rules
# ---------------------------------------------------------------------------

def test_passes_when_all_rules_met():
    passed, reason = passes_technical_filter(_snapshot())
    assert passed is True
    assert reason == ""


def test_fails_when_rsi_too_low():
    passed, reason = passes_technical_filter(_snapshot(rsi=29.9))
    assert passed is False
    assert "RSI" in reason


def test_fails_when_rsi_too_high():
    passed, reason = passes_technical_filter(_snapshot(rsi=70.1))
    assert passed is False
    assert "RSI" in reason


def test_fails_when_rsi_exactly_30():
    # Boundary: 30 is excluded (rule is strictly greater than 30)
    passed, reason = passes_technical_filter(_snapshot(rsi=30.0))
    assert passed is False


def test_fails_when_rsi_exactly_70():
    # Boundary: 70 is excluded (rule is strictly less than 70)
    passed, reason = passes_technical_filter(_snapshot(rsi=70.0))
    assert passed is False


# ---------------------------------------------------------------------------
# Uptrend rule
# ---------------------------------------------------------------------------

def test_fails_when_price_below_sma50():
    passed, reason = passes_technical_filter(_snapshot(price=155.0, sma50=160.0))
    assert passed is False
    assert "SMA50" in reason


def test_fails_when_price_equals_sma50():
    # Rule requires strictly above SMA50
    passed, reason = passes_technical_filter(_snapshot(price=160.0, sma50=160.0))
    assert passed is False


# ---------------------------------------------------------------------------
# SMA20 momentum rules
# ---------------------------------------------------------------------------

def test_fails_when_price_below_sma20():
    passed, reason = passes_technical_filter(_snapshot(price=165.0, sma20=170.0))
    assert passed is False
    assert "SMA20" in reason


def test_fails_when_sma20_not_rising():
    # sma20 <= sma20_prev means no upward momentum
    passed, reason = passes_technical_filter(_snapshot(sma20=170.0, sma20_prev=171.0))
    assert passed is False
    assert "SMA20" in reason


def test_fails_when_sma20_flat():
    passed, reason = passes_technical_filter(_snapshot(sma20=170.0, sma20_prev=170.0))
    assert passed is False


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_fails_with_missing_field():
    snapshot = {"symbol": "AAPL", "price": 175.0}  # missing rsi, sma20, sma50, etc.
    passed, reason = passes_technical_filter(snapshot)
    assert passed is False
    assert "Missing" in reason or "invalid" in reason.lower()


def test_fails_with_non_numeric_field():
    passed, reason = passes_technical_filter(_snapshot(rsi="not-a-number"))
    assert passed is False
    assert "Missing" in reason or "invalid" in reason.lower()


# ---------------------------------------------------------------------------
# passes_sell_filter tests
# ---------------------------------------------------------------------------

def _sell_snapshot(**overrides) -> dict:
    """Build a snapshot that passes the sell filter via high RSI by default."""
    base = {
        "symbol": "AAPL",
        "price": 175.0,
        "rsi": 65.0,       # >= 60 → sell signal
        "sma20": 170.0,
        "sma20_prev": 169.0,
    }
    base.update(overrides)
    return base


def test_sell_filter_passes_on_high_rsi():
    passed, reason = passes_sell_filter(_sell_snapshot(rsi=65.0))
    assert passed is True


def test_sell_filter_passes_on_price_below_sma20():
    passed, reason = passes_sell_filter(_sell_snapshot(rsi=50.0, price=165.0, sma20=170.0))
    assert passed is True


def test_sell_filter_passes_on_falling_sma20():
    passed, reason = passes_sell_filter(_sell_snapshot(rsi=50.0, price=175.0, sma20=170.0, sma20_prev=171.0))
    assert passed is True


def test_sell_filter_fails_when_no_sell_signal():
    # RSI < 60, price above SMA20, SMA20 rising → no sell signal
    passed, reason = passes_sell_filter(_sell_snapshot(rsi=50.0, price=175.0, sma20=170.0, sma20_prev=169.0))
    assert passed is False
    assert "No sell signal" in reason


def test_sell_filter_passes_at_rsi_boundary():
    # RSI exactly 60 should pass
    passed, _ = passes_sell_filter(_sell_snapshot(rsi=60.0))
    assert passed is True


def test_sell_filter_fails_with_missing_field():
    passed, reason = passes_sell_filter({"symbol": "AAPL"})
    assert passed is False
    assert "Missing" in reason or "invalid" in reason.lower()
