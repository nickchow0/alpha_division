"""Unit tests for validator.py — the pre-backtest replay gate."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

import pytest
from validator import validate_against_pattern


def _make_rows(n: int = 50, rsi_14: float = 38.0) -> list[dict]:
    """Build synthetic feature rows that match the 26-key snapshot schema."""
    return [
        {
            "price": 150.0, "volume": 1_000_000,
            "rsi_7": 35.0, "rsi_14": rsi_14, "rsi_21": 40.0,
            "mom_5d": -0.02, "mom_10d": -0.03, "mom_20d": -0.05,
            "sma_10": 148.0, "sma_20": 148.0, "sma_50": 145.0, "sma_200": 140.0,
            "dist_sma10": 0.014, "dist_sma20": 0.014, "dist_sma50": 0.034, "dist_sma200": 0.071,
            "atr_14": 3.2, "bb_width": 0.08, "dist_bb_upper": 0.04, "dist_bb_lower": 0.03,
            "vol_zscore": 1.2, "vol_ratio": 1.25,
            "macd_line": -0.5, "macd_signal": -0.3, "macd_hist": -0.2,
            "dist_52w_high": 0.15, "dist_52w_low": 0.05, "day_of_week": 1,
        }
        for _ in range(n)
    ]


_CFG = {"min_replay_signal_rate": 0.20, "min_replay_buy_rate": 0.40}

_ALWAYS_BUY = '''
def generate_signal(snapshot):
    return {"decision": "buy", "confidence": 0.7, "reasoning": "always buy"}
'''

_ALWAYS_HOLD = '''
def generate_signal(snapshot):
    return {"decision": "hold", "confidence": 0.5, "reasoning": "always hold"}
'''

_ONLY_SELL = '''
def generate_signal(snapshot):
    return {"decision": "sell", "confidence": 0.6, "reasoning": "always sell"}
'''

_RSI_BUY = '''
def generate_signal(snapshot):
    rsi = snapshot.get("rsi_14") or 50
    if rsi < 40:
        return {"decision": "buy", "confidence": 0.7, "reasoning": "RSI oversold"}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "no signal"}
'''


def test_always_buy_passes():
    assert validate_against_pattern(_ALWAYS_BUY, _make_rows(50), _CFG) is True


def test_always_hold_fails_signal_rate():
    assert validate_against_pattern(_ALWAYS_HOLD, _make_rows(50), _CFG) is False


def test_only_sell_fails_buy_rate():
    assert validate_against_pattern(_ONLY_SELL, _make_rows(50), _CFG) is False


def test_rsi_strategy_passes_when_rows_trigger():
    """Strategy that buys on rsi_14 < 40 passes when all rows have rsi_14=38."""
    assert validate_against_pattern(_RSI_BUY, _make_rows(50, rsi_14=38.0), _CFG) is True


def test_rsi_strategy_fails_when_rows_never_trigger():
    """Strategy that buys on rsi_14 < 40 fails when all rows have rsi_14=70."""
    assert validate_against_pattern(_RSI_BUY, _make_rows(50, rsi_14=70.0), _CFG) is False


def test_empty_rows_fails():
    assert validate_against_pattern(_ALWAYS_BUY, [], _CFG) is False


def test_invalid_code_fails():
    assert validate_against_pattern("not valid python!!!", _make_rows(50), _CFG) is False


def test_custom_thresholds_respected():
    """A strict config (signal_rate=1.0) rejects a strategy that sometimes holds."""
    strict_cfg = {"min_replay_signal_rate": 1.0, "min_replay_buy_rate": 0.40}
    assert validate_against_pattern(_RSI_BUY, _make_rows(50, rsi_14=38.0), strict_cfg) is True
    assert validate_against_pattern(_RSI_BUY, _make_rows(50, rsi_14=50.0), strict_cfg) is False
