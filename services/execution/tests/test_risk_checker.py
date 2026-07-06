import math
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from risk_checker import (
    check_trading_window,
    check_position_rules,
    check_position_limit,
    calculate_qty,
    check_circuit_breaker,
)

_ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# check_trading_window
# ---------------------------------------------------------------------------

def test_allows_trading_at_10am_on_weekday():
    now = datetime(2026, 5, 18, 10, 30, tzinfo=_ET)  # Monday 10:30am ET
    allowed, reason = check_trading_window(now)
    assert allowed is True
    assert reason == ""


def test_blocks_during_blackout_window():
    now = datetime(2026, 5, 18, 9, 45, tzinfo=_ET)  # Monday 9:45am ET
    allowed, reason = check_trading_window(now)
    assert allowed is False
    assert "blackout" in reason.lower()


def test_blocks_before_market_open():
    now = datetime(2026, 5, 18, 9, 0, tzinfo=_ET)  # Monday 9:00am ET
    allowed, reason = check_trading_window(now)
    assert allowed is False
    assert "market hours" in reason.lower()


def test_blocks_after_market_close():
    now = datetime(2026, 5, 18, 16, 1, tzinfo=_ET)  # Monday 4:01pm ET
    allowed, reason = check_trading_window(now)
    assert allowed is False
    assert "market hours" in reason.lower()


def test_blocks_on_saturday():
    now = datetime(2026, 5, 16, 11, 0, tzinfo=_ET)  # Saturday 11am ET
    allowed, reason = check_trading_window(now)
    assert allowed is False
    assert "weekend" in reason.lower()


def test_blocks_on_sunday():
    now = datetime(2026, 5, 17, 11, 0, tzinfo=_ET)  # Sunday 11am ET
    allowed, reason = check_trading_window(now)
    assert allowed is False
    assert "weekend" in reason.lower()


def test_allows_trading_exactly_at_blackout_end():
    # 10:00:00 ET is the first second trading is allowed
    now = datetime(2026, 5, 18, 10, 0, 0, tzinfo=_ET)
    allowed, reason = check_trading_window(now)
    assert allowed is True


def test_blocks_at_exact_market_close():
    # 16:00 ET is the close — the window is strictly < 16:00
    now = datetime(2026, 5, 18, 16, 0, 0, tzinfo=_ET)
    allowed, reason = check_trading_window(now)
    assert allowed is False


def test_blocks_at_exact_market_open():
    # 9:30 ET — within blackout window
    now = datetime(2026, 5, 18, 9, 30, 0, tzinfo=_ET)
    allowed, reason = check_trading_window(now)
    assert allowed is False
    assert "blackout" in reason.lower()


# ---------------------------------------------------------------------------
# check_position_rules
# ---------------------------------------------------------------------------

def test_buy_fails_when_already_holding():
    positions = {"AAPL": 5}
    ok, reason = check_position_rules("AAPL", "buy", positions)
    assert ok is False
    assert "AAPL" in reason


def test_buy_allowed_when_not_holding():
    positions = {"MSFT": 3}
    ok, reason = check_position_rules("AAPL", "buy", positions)
    assert ok is True
    assert reason == ""


def test_sell_fails_when_not_holding():
    positions = {"MSFT": 3}
    ok, reason = check_position_rules("AAPL", "sell", positions)
    assert ok is False
    assert "AAPL" in reason


def test_sell_allowed_when_holding():
    positions = {"AAPL": 5}
    ok, reason = check_position_rules("AAPL", "sell", positions)
    assert ok is True
    assert reason == ""


def test_buy_allowed_when_positions_empty():
    ok, reason = check_position_rules("AAPL", "buy", {})
    assert ok is True


def test_buy_rejected_when_symbol_is_already_short():
    positions = {"AAPL": -5}  # short position
    ok, reason = check_position_rules("AAPL", "buy", positions)
    assert ok is False
    assert "AAPL" in reason


def test_position_rules_blocks_unknown_side():
    ok, reason = check_position_rules("AAPL", "foobar", {"AAPL": 5})
    assert ok is False
    assert "foobar" in reason


# ---------------------------------------------------------------------------
# check_position_rules — short and cover
# ---------------------------------------------------------------------------

def test_short_allowed_when_no_position():
    ok, _ = check_position_rules("AAPL", "short", {})
    assert ok is True


def test_short_rejected_when_already_long():
    positions = {"AAPL": 5}
    ok, reason = check_position_rules("AAPL", "short", positions)
    assert ok is False
    assert "AAPL" in reason
    assert "long" in reason


def test_short_rejected_when_already_short():
    positions = {"AAPL": -5}
    ok, reason = check_position_rules("AAPL", "short", positions)
    assert ok is False
    assert "AAPL" in reason
    assert "short" in reason


def test_cover_allowed_when_short():
    positions = {"AAPL": -5}
    ok, _ = check_position_rules("AAPL", "cover", positions)
    assert ok is True


def test_cover_rejected_when_long():
    positions = {"AAPL": 5}
    ok, reason = check_position_rules("AAPL", "cover", positions)
    assert ok is False
    assert "AAPL" in reason


def test_cover_rejected_when_no_position():
    ok, reason = check_position_rules("AAPL", "cover", {})
    assert ok is False
    assert "AAPL" in reason


# ---------------------------------------------------------------------------
# check_position_limit
# ---------------------------------------------------------------------------

def test_position_limit_allows_buy_under_max():
    positions = {"AAPL": 5, "MSFT": 3, "GOOGL": 2}  # 3 longs
    ok, _ = check_position_limit(positions, "buy", max_positions=5)
    assert ok is True


def test_position_limit_blocks_buy_at_max():
    positions = {"AAPL": 5, "MSFT": 3, "GOOGL": 2, "AMZN": 1, "TSLA": 4}  # 5 longs
    ok, reason = check_position_limit(positions, "buy", max_positions=5)
    assert ok is False
    assert "5" in reason


def test_position_limit_allows_buy_with_exactly_four_longs():
    positions = {f"SYM{i}": 1 for i in range(4)}
    ok, _ = check_position_limit(positions, "buy", max_positions=5)
    assert ok is True


def test_position_limit_ignores_shorts_when_counting_longs():
    # 3 longs and 3 shorts — buy limit counts only longs
    positions = {"A": 5, "B": 3, "C": 2, "D": -1, "E": -2, "F": -3}
    ok, _ = check_position_limit(positions, "buy", max_positions=5)
    assert ok is True


def test_position_limit_allows_sell_regardless_of_count():
    positions = {"AAPL": 5, "MSFT": 3, "GOOGL": 2, "AMZN": 1, "TSLA": 4}
    ok, _ = check_position_limit(positions, "sell", max_positions=5)
    assert ok is True


def test_position_limit_allows_cover_regardless_of_count():
    positions = {"A": -1, "B": -2, "C": -3, "D": -4, "E": -5}
    ok, _ = check_position_limit(positions, "cover", max_short_positions=5)
    assert ok is True


def test_position_limit_blocks_short_at_max():
    positions = {"A": -1, "B": -2, "C": -3}  # 3 shorts
    ok, reason = check_position_limit(positions, "short", max_short_positions=3)
    assert ok is False
    assert "3" in reason


def test_position_limit_allows_short_under_max():
    positions = {"A": -1, "B": -2}  # 2 shorts
    ok, _ = check_position_limit(positions, "short", max_short_positions=3)
    assert ok is True


def test_position_limit_ignores_longs_when_counting_shorts():
    # 5 longs and 2 shorts — short limit counts only shorts
    positions = {"A": 5, "B": 3, "C": 2, "D": 1, "E": 1, "F": -1, "G": -2}
    ok, _ = check_position_limit(positions, "short", max_short_positions=3)
    assert ok is True


def test_position_limit_respects_custom_long_max():
    positions = {f"SYM{i}": 1 for i in range(10)}
    ok, reason = check_position_limit(positions, "buy", max_positions=10)
    assert ok is False
    assert "10" in reason
    ok2, _ = check_position_limit(positions, "buy", max_positions=15)
    assert ok2 is True


# ---------------------------------------------------------------------------
# calculate_qty
# ---------------------------------------------------------------------------

def test_calculate_qty_basic():
    # 2% of $100,000 = $2,000 / $175.50 = floor(11.39) = 11
    result = calculate_qty(100_000.0, 175.50, risk_pct=0.02)
    assert result == 11


def test_calculate_qty_four_pct():
    # 4% of $100,000 = $4,000 / $175.50 = floor(22.79) = 22
    result = calculate_qty(100_000.0, 175.50, risk_pct=0.04)
    assert result == 22


def test_calculate_qty_returns_zero_when_portfolio_too_small():
    # 2% of $500 = $10 / $150.0 = floor(0.066) = 0
    result = calculate_qty(500.0, 150.0, risk_pct=0.02)
    assert result == 0


def test_calculate_qty_rounds_down():
    # 2% of $10,000 = $200 / $150.0 = floor(1.333) = 1
    result = calculate_qty(10_000.0, 150.0, risk_pct=0.02)
    assert result == 1


def test_calculate_qty_zero_price_returns_zero():
    result = calculate_qty(100_000.0, 0.0)
    assert result == 0


def test_calculate_qty_negative_price_returns_zero():
    result = calculate_qty(100_000.0, -10.0)
    assert result == 0


def test_calculate_qty_negative_portfolio_returns_zero():
    result = calculate_qty(-100_000.0, 100.0)
    assert result == 0


# ---------------------------------------------------------------------------
# check_circuit_breaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_not_triggered_when_profitable():
    ok, reason = check_circuit_breaker(50.0)
    assert ok is True
    assert reason == ""


def test_circuit_breaker_not_triggered_just_below_limit():
    ok, reason = check_circuit_breaker(-199.99)
    assert ok is True


def test_circuit_breaker_triggered_at_exactly_limit():
    ok, reason = check_circuit_breaker(-200.0)
    assert ok is False
    assert "200" in reason


def test_circuit_breaker_triggered_above_limit():
    ok, reason = check_circuit_breaker(-250.0)
    assert ok is False
    assert "250" in reason


def test_circuit_breaker_not_triggered_at_zero():
    ok, reason = check_circuit_breaker(0.0)
    assert ok is True
