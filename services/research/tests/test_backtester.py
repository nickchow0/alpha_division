# services/research/tests/test_backtester.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from backtester import compute_indicators_series, run_backtest

SLIPPAGE = 0.0005


def _make_bars(n: int, start_price: float = 100.0) -> list[dict]:
    """Generate n bars with a gentle uptrend. Need n >= 210 for full indicator set."""
    bars = []
    price = start_price
    for i in range(n):
        bars.append({
            "t": f"2024-01-{(i % 28) + 1:02d}",
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

SHORT_CODE = """
def generate_signal(snapshot):
    return {"decision": "short", "confidence": 0.80, "reasoning": "always short"}
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
        bars = _make_bars(211)
        result = compute_indicators_series(bars)
        self.assertGreater(len(result), 0)

    def test_snapshot_has_all_required_keys(self):
        bars = _make_bars(211)
        result = compute_indicators_series(bars)
        snap = result[0]
        for key in ("price", "rsi", "sma20", "sma50", "sma20_prev", "sma20_prev2",
                    "volume", "volume_avg"):
            self.assertIn(key, snap, f"Missing key: {key}")

    def test_snapshot_has_internal_navigation_keys(self):
        bars = _make_bars(211)
        result = compute_indicators_series(bars)
        snap = result[0]
        self.assertIn("_bar_idx", snap)
        self.assertIn("_open", snap)

    def test_excludes_last_bar(self):
        bars = _make_bars(211)
        result = compute_indicators_series(bars)
        last_idx = len(bars) - 1
        for snap in result:
            self.assertLess(snap["_bar_idx"], last_idx)

    def test_next_open_matches_bars(self):
        bars = _make_bars(211)
        result = compute_indicators_series(bars)
        for snap in result:
            idx = snap["_bar_idx"]
            self.assertAlmostEqual(snap["_open"], bars[idx + 1]["o"])

    def test_returns_something_for_210_bars(self):
        bars = _make_bars(211)
        result = compute_indicators_series(bars)
        self.assertGreater(len(result), 0)

    def test_snapshots_contain_ml_keys(self):
        bars = _make_bars(211)
        snapshots = compute_indicators_series(bars)
        self.assertGreater(len(snapshots), 0)
        snap = snapshots[-1]
        for key in ("rsi_7", "rsi_14", "rsi_21", "macd_hist", "atr_14",
                    "vol_zscore", "vol_ratio", "dist_sma20", "day_of_week"):
            self.assertIn(key, snap, f"Missing ML key: {key}")

    def test_legacy_keys_still_present(self):
        bars = _make_bars(211)
        snapshots = compute_indicators_series(bars)
        self.assertGreater(len(snapshots), 0)
        snap = snapshots[-1]
        for key in ("price", "rsi", "sma20", "sma50", "sma20_prev",
                    "sma20_prev2", "volume", "volume_avg"):
            self.assertIn(key, snap, f"Missing legacy key: {key}")


class TestRunBacktest(unittest.TestCase):
    def test_zero_trades_when_hold_always(self):
        bars = _make_bars(211)
        metrics, trades = run_backtest(HOLD_CODE, bars, DEFAULT_PARAMS)
        self.assertEqual(trades, [])
        self.assertEqual(metrics["trade_count"], 0)
        self.assertAlmostEqual(metrics["total_return_pct"], 0.0)
        self.assertEqual(metrics["win_rate_pct"], 0.0)
        self.assertIsNone(metrics["sharpe_ratio"])

    def test_metrics_keys_present(self):
        bars = _make_bars(211)
        metrics, _ = run_backtest(HOLD_CODE, bars, DEFAULT_PARAMS)
        for key in ("total_return_pct", "sharpe_ratio", "max_drawdown_pct",
                    "win_rate_pct", "trade_count", "avg_hold_bars"):
            self.assertIn(key, metrics, f"Missing metric: {key}")

    def test_position_sizing_confidence_scaled(self):
        """position_size ≈ confidence × max_position_pct × initial_capital"""
        bars = _make_bars(215)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 3, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        self.assertGreater(len(trades), 0)
        # confidence=0.80, max_position_pct=0.15, capital=100000
        # position_size = 0.80 * 0.15 * 100000 = 12000 (approx, within $300)
        first = trades[0]
        self.assertAlmostEqual(first["position_size"], 12000.0, delta=300)

    def test_stop_loss_exit(self):
        """Position exits with stop_loss reason when close drops below stop price."""
        bars = _make_bars(215, start_price=100.0)
        # Drop a bar well below stop price after some initial trading
        bars[212]["c"] = 88.0
        bars[212]["l"] = 88.0
        params = {**DEFAULT_PARAMS, "stop_loss_pct": 0.05, "max_hold_bars": 100}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        stop_trades = [t for t in trades if t["exit_reason"] == "stop_loss"]
        self.assertGreater(len(stop_trades), 0)
        self.assertLess(stop_trades[0]["pnl"], 0)  # stop loss is always a loss

    def test_max_hold_exit(self):
        """Position exits with max_hold reason after max_hold_bars bars."""
        bars = _make_bars(215, start_price=100.0)
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
        bars = _make_bars(211)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 100, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(sell_on_third, bars, params)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_reason"], "signal")

    def test_returns_empty_for_insufficient_bars(self):
        """Backtest on fewer than 210 bars produces zero trades."""
        bars = _make_bars(40)
        metrics, trades = run_backtest(BUY_CODE, bars, DEFAULT_PARAMS)
        self.assertEqual(trades, [])
        self.assertEqual(metrics["trade_count"], 0)

    def test_long_trade_has_side_long(self):
        """Long trades record side as 'long'."""
        bars = _make_bars(215)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 3, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(BUY_CODE, bars, params)
        self.assertGreater(len(trades), 0)
        for t in trades:
            self.assertEqual(t["side"], "long")

    def test_short_trade_has_side_short(self):
        """Short trades record side as 'short'."""
        bars = _make_bars(215)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 3, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(SHORT_CODE, bars, params)
        self.assertGreater(len(trades), 0)
        for t in trades:
            self.assertEqual(t["side"], "short")

    def test_short_loses_when_price_rises(self):
        """Short position loses money when price trends up."""
        bars = _make_bars(215)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 3, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(SHORT_CODE, bars, params)
        self.assertGreater(len(trades), 0)
        self.assertTrue(all(t["pnl"] < 0 for t in trades))

    def test_short_stop_loss_triggered_when_price_rises(self):
        """Short stop loss triggers when price rises above entry * (1 + stop_loss_pct)."""
        bars = _make_bars(215, start_price=100.0)
        # Default uptrend (0.1%/bar) naturally rises past a 5% stop within ~50 bars
        params = {**DEFAULT_PARAMS, "stop_loss_pct": 0.05, "max_hold_bars": 100}
        metrics, trades = run_backtest(SHORT_CODE, bars, params)
        stop_trades = [t for t in trades if t["exit_reason"] == "stop_loss"]
        self.assertGreater(len(stop_trades), 0)
        self.assertLess(stop_trades[0]["pnl"], 0)

    def test_cover_signal_closes_short(self):
        """Cover signal exits a short position with exit_reason='signal'."""
        short_then_cover = """
_count = [0]
def generate_signal(snapshot):
    _count[0] += 1
    if _count[0] == 1:
        return {"decision": "short", "confidence": 0.80, "reasoning": "short"}
    if _count[0] == 3:
        return {"decision": "cover", "confidence": 0.50, "reasoning": "cover"}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "hold"}
"""
        bars = _make_bars(211)
        params = {**DEFAULT_PARAMS, "max_hold_bars": 100, "stop_loss_pct": 0.50}
        metrics, trades = run_backtest(short_then_cover, bars, params)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["side"], "short")
        self.assertEqual(trades[0]["exit_reason"], "signal")
