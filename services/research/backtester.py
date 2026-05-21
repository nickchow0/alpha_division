# services/research/backtester.py
import statistics
from typing import Optional

import pandas as pd
import ta.momentum
import ta.trend
import ta.volatility

from strategy import validate_strategy_code, load_strategy, execute_strategy

_SLIPPAGE = 0.0005  # 0.05% per side
_MIN_BARS = 210      # SMA200 needs 200 bars


def compute_indicators_series(bars: list[dict]) -> list[dict]:
    """
    Compute rolling indicators across all bars and return one snapshot per bar
    where core legacy indicators are valid. Each snapshot includes:
      _bar_idx  — index into bars list
      _open     — next bar's open (fill price for trades)

    Returns [] if fewer than _MIN_BARS bars.
    The last bar is always excluded (no next bar to fill at).
    All ML feature values that are NaN are included as None.
    """
    if len(bars) < _MIN_BARS:
        return []

    closes  = pd.Series([float(b["c"]) for b in bars])
    highs   = pd.Series([float(b["h"]) for b in bars])
    lows    = pd.Series([float(b["l"]) for b in bars])
    volumes = pd.Series([float(b["v"]) for b in bars])

    rsi_7_s  = ta.momentum.RSIIndicator(close=closes, window=7).rsi()
    rsi_14_s = ta.momentum.RSIIndicator(close=closes, window=14).rsi()
    rsi_21_s = ta.momentum.RSIIndicator(close=closes, window=21).rsi()

    sma_10_s  = ta.trend.SMAIndicator(close=closes, window=10).sma_indicator()
    sma_20_s  = ta.trend.SMAIndicator(close=closes, window=20).sma_indicator()
    sma_50_s  = ta.trend.SMAIndicator(close=closes, window=50).sma_indicator()
    sma_200_s = ta.trend.SMAIndicator(close=closes, window=200).sma_indicator()

    mom_5d_s  = closes.pct_change(5)
    mom_10d_s = closes.pct_change(10)
    mom_20d_s = closes.pct_change(20)

    atr_14_s   = ta.volatility.AverageTrueRange(
        high=highs, low=lows, close=closes, window=14
    ).average_true_range()
    bb_ind     = ta.volatility.BollingerBands(close=closes, window=20, window_dev=2)
    bb_upper_s = bb_ind.bollinger_hband()
    bb_lower_s = bb_ind.bollinger_lband()
    bb_mid_s   = bb_ind.bollinger_mavg()

    vol_mean_s = volumes.rolling(window=20).mean()
    vol_std_s  = volumes.rolling(window=20).std()
    vol_avg5_s = volumes.rolling(window=5).mean()

    macd_ind      = ta.trend.MACD(close=closes)
    macd_line_s   = macd_ind.macd()
    macd_signal_s = macd_ind.macd_signal()
    macd_hist_s   = macd_ind.macd_diff()

    high_252_s = closes.rolling(window=252, min_periods=50).max()
    low_252_s  = closes.rolling(window=252, min_periods=50).min()

    def _f(val) -> Optional[float]:
        return None if pd.isna(val) else float(val)

    snapshots = []
    for i in range(len(bars) - 1):
        # Skip until core legacy indicators are valid
        if (pd.isna(rsi_14_s.iloc[i]) or pd.isna(sma_20_s.iloc[i])
                or pd.isna(sma_50_s.iloc[i]) or i < 2
                or pd.isna(sma_20_s.iloc[i - 1]) or pd.isna(sma_20_s.iloc[i - 2])
                or pd.isna(vol_mean_s.iloc[i])):
            continue

        c    = closes.iloc[i]
        s10  = sma_10_s.iloc[i]
        s20  = sma_20_s.iloc[i]
        s50  = sma_50_s.iloc[i]
        s200 = sma_200_s.iloc[i]

        bb_up  = bb_upper_s.iloc[i]
        bb_lo  = bb_lower_s.iloc[i]
        bb_mid = bb_mid_s.iloc[i]
        bb_w   = (bb_up - bb_lo) if not (pd.isna(bb_up) or pd.isna(bb_lo)) else float("nan")

        vm  = vol_mean_s.iloc[i]
        vs  = vol_std_s.iloc[i]
        va5 = vol_avg5_s.iloc[i]
        v   = volumes.iloc[i]
        vol_z = float((v - vm) / vs) if (not pd.isna(vs) and vs != 0) else None
        vol_r = float(v / va5)       if (not pd.isna(va5) and va5 != 0) else None

        h252 = high_252_s.iloc[i]
        l252 = low_252_s.iloc[i]

        try:
            dow = min(pd.Timestamp(bars[i]["t"]).weekday(), 4)
        except Exception:
            dow = 0

        snapshots.append({
            # Legacy keys
            "price":       float(c),
            "rsi":         float(rsi_14_s.iloc[i]),
            "sma20":       float(s20),
            "sma50":       float(s50),
            "sma20_prev":  float(sma_20_s.iloc[i - 1]),
            "sma20_prev2": float(sma_20_s.iloc[i - 2]),
            "volume":      float(v),
            "volume_avg":  float(vm) if not pd.isna(vm) else None,
            # ML feature keys
            "rsi_7":        _f(rsi_7_s.iloc[i]),
            "rsi_14":       float(rsi_14_s.iloc[i]),
            "rsi_21":       _f(rsi_21_s.iloc[i]),
            "mom_5d":       _f(mom_5d_s.iloc[i]),
            "mom_10d":      _f(mom_10d_s.iloc[i]),
            "mom_20d":      _f(mom_20d_s.iloc[i]),
            "sma_10":       _f(s10),
            "sma_20":       _f(s20),
            "sma_50":       _f(s50),
            "sma_200":      _f(s200),
            "dist_sma10":   float((c - s10) / s10)   if not (pd.isna(s10)  or s10  == 0) else None,
            "dist_sma20":   float((c - s20) / s20)   if not (pd.isna(s20)  or s20  == 0) else None,
            "dist_sma50":   float((c - s50) / s50)   if not (pd.isna(s50)  or s50  == 0) else None,
            "dist_sma200":  float((c - s200) / s200) if not (pd.isna(s200) or s200 == 0) else None,
            "atr_14":       _f(atr_14_s.iloc[i]),
            "bb_width":     float(bb_w / bb_mid) if not (pd.isna(bb_w) or pd.isna(bb_mid) or bb_mid == 0) else None,
            "dist_bb_upper": float((bb_up - c) / c) if (not pd.isna(bb_up) and c != 0) else None,
            "dist_bb_lower": float((c - bb_lo) / c) if (not pd.isna(bb_lo) and c != 0) else None,
            "vol_zscore":   vol_z,
            "vol_ratio":    vol_r,
            "macd_line":    _f(macd_line_s.iloc[i]),
            "macd_signal":  _f(macd_signal_s.iloc[i]),
            "macd_hist":    _f(macd_hist_s.iloc[i]),
            "dist_52w_high": float((h252 - c) / c) if (not pd.isna(h252) and c != 0) else None,
            "dist_52w_low":  float((c - l252) / c) if (not pd.isna(l252) and c != 0) else None,
            "day_of_week":  dow,
            # Backtest-internal keys
            "_bar_idx": i,
            "_open":    float(bars[i + 1]["o"]),
        })
    return snapshots


def run_backtest(
    strategy_code: str,
    bars: list[dict],
    params: dict,
) -> tuple[dict, list[dict]]:
    """
    Run a vectorized backtest of strategy_code against bars.

    params keys:
        initial_capital   — starting portfolio value (default 100_000)
        max_position_pct  — max fraction of portfolio per trade (default 0.15)
        stop_loss_pct     — stop loss fraction below entry (default 0.05)
        max_hold_bars     — max bars to hold a position (default 20)

    Returns (metrics_dict, trades_list).
    metrics keys: total_return_pct, sharpe_ratio, max_drawdown_pct,
                  win_rate_pct, trade_count, avg_hold_bars
    trades dicts: entry_bar, exit_bar, entry_price, exit_price,
                  position_size, pnl, exit_reason, side
    """
    validate_strategy_code(strategy_code)
    fn = load_strategy(strategy_code)

    snapshots = compute_indicators_series(bars)
    if not snapshots:
        return _empty_metrics(), []

    initial_capital = float(params.get("initial_capital", 100_000))
    max_position_pct = float(params.get("max_position_pct", 0.15))
    stop_loss_pct = float(params.get("stop_loss_pct", 0.05))
    max_hold_bars = int(params.get("max_hold_bars", 20))

    cash = initial_capital
    position: Optional[dict] = None
    portfolio_history: list[float] = []
    trades: list[dict] = []

    for snap in snapshots:
        bar_idx = snap["_bar_idx"]
        next_open = snap["_open"]
        current_close = snap["price"]

        # Mark-to-market portfolio value
        mtm = cash + (position["shares"] * current_close if position else 0.0)
        portfolio_history.append(mtm)

        # --- Check forced exits BEFORE generating signal ---
        if position is not None:
            bars_held = bar_idx - position["entry_bar"]
            stop_price = position["entry_price"] * (1.0 - stop_loss_pct)

            force_exit = None
            if current_close <= stop_price:
                force_exit = "stop_loss"
            elif bars_held >= max_hold_bars:
                force_exit = "max_hold"

            if force_exit:
                if force_exit == "stop_loss":
                    # Cap fill at stop price (realistic: stop orders don't fill above stop)
                    stop_price_fill = position["entry_price"] * (1.0 - stop_loss_pct)
                    fill_price = min(next_open, stop_price_fill)
                else:
                    fill_price = next_open
                exit_price = fill_price * (1.0 - _SLIPPAGE)
                pnl = (exit_price - position["entry_price"]) * position["shares"]
                cash += position["shares"] * exit_price
                trades.append({
                    "side": "buy",
                    "entry_bar": position["entry_bar"],
                    "exit_bar": bar_idx + 1,
                    "entry_price": round(position["entry_price"], 4),
                    "exit_price": round(exit_price, 4),
                    "position_size": position["position_size"],
                    "pnl": round(pnl, 4),
                    "exit_reason": force_exit,
                })
                position = None
                continue  # Skip signal generation this bar

        # --- Generate signal ---
        signal_input = {k: v for k, v in snap.items() if not k.startswith("_")}
        try:
            result = execute_strategy(fn, signal_input)
        except ValueError:
            # Invalid schema — treat as hold for this bar
            continue

        decision = result["decision"]
        confidence = float(result["confidence"])

        if position is None and decision == "buy":
            # Enter long position at next bar's open
            portfolio_value = cash
            position_size_dollars = confidence * max_position_pct * portfolio_value
            if position_size_dollars > cash:
                position_size_dollars = cash
            if position_size_dollars <= 0:
                continue
            entry_price = next_open * (1.0 + _SLIPPAGE)
            shares = position_size_dollars / entry_price
            cost = shares * entry_price
            cash -= cost
            position = {
                "entry_bar": bar_idx + 1,
                "entry_price": entry_price,
                "shares": shares,
                "position_size": round(position_size_dollars, 4),
            }

        elif position is not None and decision == "sell":
            # Exit long position at next bar's open
            exit_price = next_open * (1.0 - _SLIPPAGE)
            pnl = (exit_price - position["entry_price"]) * position["shares"]
            cash += position["shares"] * exit_price
            trades.append({
                "side": "buy",
                "entry_bar": position["entry_bar"],
                "exit_bar": bar_idx + 1,
                "entry_price": round(position["entry_price"], 4),
                "exit_price": round(exit_price, 4),
                "position_size": position["position_size"],
                "pnl": round(pnl, 4),
                "exit_reason": "signal",
            })
            position = None

    # Close any remaining open position at last bar's close
    if position is not None:
        last_close = float(bars[-1]["c"])
        exit_price = last_close * (1.0 - _SLIPPAGE)
        pnl = (exit_price - position["entry_price"]) * position["shares"]
        cash += position["shares"] * exit_price
        trades.append({
            "side": "buy",
            "entry_bar": position["entry_bar"],
            "exit_bar": len(bars) - 1,
            "entry_price": round(position["entry_price"], 4),
            "exit_price": round(exit_price, 4),
            "position_size": position["position_size"],
            "pnl": round(pnl, 4),
            "exit_reason": "signal",
        })

    final_value = cash
    metrics = _compute_metrics(trades, initial_capital, final_value, portfolio_history)
    return metrics, trades


def _empty_metrics() -> dict:
    return {
        "total_return_pct": 0.0,
        "sharpe_ratio": None,
        "max_drawdown_pct": 0.0,
        "win_rate_pct": 0.0,
        "trade_count": 0,
        "avg_hold_bars": None,
    }


def _compute_metrics(
    trades: list[dict],
    initial_capital: float,
    final_value: float,
    portfolio_history: list[float],
) -> dict:
    trade_count = len(trades)

    if trade_count == 0:
        return _empty_metrics()

    total_return_pct = round(
        (final_value - initial_capital) / initial_capital * 100, 4
    )

    winning = [t for t in trades if t["pnl"] > 0]
    win_rate_pct = round(len(winning) / trade_count * 100, 4)

    hold_bars = [
        t["exit_bar"] - t["entry_bar"]
        for t in trades
        if t.get("exit_bar") is not None
    ]
    avg_hold_bars = round(sum(hold_bars) / len(hold_bars), 2) if hold_bars else None

    max_drawdown_pct = _compute_max_drawdown(portfolio_history)

    # Sharpe: annualised from per-trade returns (return = pnl / position_size)
    trade_returns = [
        t["pnl"] / t["position_size"]
        for t in trades
        if t.get("position_size") and t["position_size"] > 0
    ]
    if len(trade_returns) < 2:
        sharpe_ratio = None
    else:
        mean_r = statistics.mean(trade_returns)
        std_r = statistics.stdev(trade_returns)
        if std_r == 0:
            sharpe_ratio = None
        else:
            sharpe_ratio = round(mean_r / std_r * (252 ** 0.5), 4)

    return {
        "total_return_pct": total_return_pct,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown_pct": max_drawdown_pct,
        "win_rate_pct": win_rate_pct,
        "trade_count": trade_count,
        "avg_hold_bars": avg_hold_bars,
    }


def _compute_max_drawdown(portfolio_history: list[float]) -> float:
    if not portfolio_history:
        return 0.0
    peak = portfolio_history[0]
    max_dd = 0.0
    for value in portfolio_history:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak * 100
            if drawdown > max_dd:
                max_dd = drawdown
    return round(max_dd, 4)
