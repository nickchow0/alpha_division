# services/research/tests/test_backtester.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from backtester import compute_indicators_series, run_backtest

SLIPPAGE = 0.0005


def _make_bars(n: int, start_price: float = 100.0) -> list[dict]:
    """Generate n bars with a gentle uptrend. Need n >= 52 for indicators."""
    bars = []
    price = start_price
    for _ in range(n):
        bars.append({
            "o": round(price * 0.999, 4),
            "h": round(price * 1.010, 4),
            "l": round(price * 0.990, 4),
            "c": round(price, 4),
            "v": 1_000_000,
        })
        price = round(price * 1.001, 4)
    return bars


HOLD_CODE = """
def generate_signal(snapshot):
    return {"decision": "hold", "confidence": 0.5, "reasoning": "never trade"}
"""

BUY_CODE = """
def generate_signal(snapshot):
    return {"decision": "buy", "confidence": 0.80, "reasoning": "always buy"}
"""

DEFAULT_PARAMS = {
    "initial_capital": 100_000,
    "max_position_pct": 0.15,
    "stop_loss_pct": 0.05,
    "max_hold_bars": 20,
}


class TestComputeIndicatorsSeries(unittest.TestCase):
    def test_returns_empty_for_fewer_than_52_bars(self):
        bars = _make_bars(51)
        result = compute_indicators_series(bars)
        self.assertEqual(result, [])

    def test_returns_snapshots_for_sufficient_bars(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        self.assertGreater(len(result), 0)

    def test_snapshot_has_all_required_keys(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        snap = result[0]
        for key in ("price", "rsi", "sma20", "sma50", "sma20_prev", "sma20_prev2",
                    "volume", "volume_avg"):
            self.assertIn(key, snap, f"Missing key: {key}")

    def test_snapshot_has_internal_navigation_keys(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        snap = result[0]
        self.assertIn("_bar_idx", snap)
        self.assertIn("_open", snap)

    def test_excludes_last_bar(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        last_idx = len(bars) - 1
        for snap in result:
            self.assertLess(snap["_bar_idx"], last_idx)

    def test_next_open_matches_bars(self):
        bars = _make_bars(60)
        result = compute_indicators_series(bars)
        for snap in result:
            idx = snap["_bar_idx"]
            self.assertAlmostEqual(snap["_open"], bars[idx + 1]["o"])


class TestRunBacktest(unittest.TestCase):
    def test_zero_trades_when_hold_always(self):
        bars = _make_bars(60)
        metrics, trades = run_backtest(HOLD_CODE, bars, DEFAULT_PARAMS)
        self.assertEqual(trades, [])
        self.assertEqual(metrics["trade_count"], 0)
        self.assertAlmostEqual(metrics["total_return_pct"], 0.0)
        self.assertEqual(metrics["win_rate_pct"], 0.0)
        self.assertIsNone(metrics["sharpe_ratio"])

    def test_metrics_keys_present(self):
        bars = _make_bars(60)
        metrics, _ = run_backtest(HOLD_CODE, bars, DEFAULT_PARAMS)
        for key in ("total_return_pct", "sharpe_ratio", "max_drawdown_pct",
                    "win_rate_pct", "trade_count", "avg_hold_bars"):
            self.assertIn(key, metrics, f"Missing metric: {key}")

    def test_position_sizing_confidence_scaled(self):
        """position_size ≈ confidence × max_position_pct × initial_capital"""
        bars = _make_bars(80)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 3, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        self.assertGreater(len(trades), 0)
        # confidence=0.80, max_position_pct=0.15, capital=100000
        # position_size = 0.80 * 0.15 * 100000 = 12000 (approx, within $300)
        first = trades[0]
        self.assertAlmostEqual(first["position_size"], 12000.0, delta=300)

    def test_stop_loss_exit(self):
        """Position exits with stop_loss reason when close drops below stop price."""
        bars = _make_bars(65, start_price=100.0)
        # Entry fills at bar 50 open (~100.0), stop at ~95
        # Drop bar 52 well below stop
        bars[52]["c"] = 88.0
        bars[52]["l"] = 88.0
        params = {**DEFAULT_PARAMS, "stop_loss_pct": 0.05, "max_hold_bars": 100}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        stop_trades = [t for t in trades if t["exit_reason"] == "stop_loss"]
        self.assertGreater(len(stop_trades), 0)
        self.assertLess(stop_trades[0]["pnl"], 0)  # stop loss is always a loss

    def test_max_hold_exit(self):
        """Position exits with max_hold reason after max_hold_bars bars."""
        bars = _make_bars(80, start_price=100.0)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 3, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        max_hold_trades = [t for t in trades if t["exit_reason"] == "max_hold"]
        self.assertGreater(len(max_hold_trades), 0)
        # exit_bar - entry_bar == 4 for max_hold_bars=3
        first = max_hold_trades[0]
        self.assertEqual(first["exit_bar"] - first["entry_bar"], 4)

    def test_signal_exit(self):
        """Position exits with signal reason when sell signal received."""
        sell_on_third = """
_count = [0]
def generate_signal(snapshot):
    _count[0] += 1
    if _count[0] == 1:
        return {"decision": "buy", "confidence": 0.80, "reasoning": "buy"}
    if _count[0] == 3:
        return {"decision": "sell", "confidence": 0.50, "reasoning": "sell"}
    return {"decision": "hold", "confidence": 0.50, "reasoning": "hold"}
"""
        bars = _make_bars(60)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 100, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(sell_on_third, bars, params)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "signal")

    def test_returns_empty_for_insufficient_bars(self):
        """Backtest on fewer than 52 bars produces zero trades."""
        bars = _make_bars(40)
        metrics, trades = run_backtest(BUY_CODE, bars, DEFAULT_PARAMS)
        self.assertEqual(trades, [])
        self.assertEqual(metrics["trade_count"], 0)
