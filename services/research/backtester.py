# services/research/backtester.py
import statistics
from typing import Optional

import pandas as pd
import ta.momentum
import ta.trend

from strategy import validate_strategy_code, load_strategy, execute_strategy

_SLIPPAGE = 0.0005  # 0.05% per side
_MIN_BARS = 52      # Minimum bars for a meaningful indicator series


def compute_indicators_series(bars: list[dict]) -> list[dict]:
    """
    Compute rolling indicators across all bars and return snapshots for every bar
    where all indicators are valid. Each snapshot includes internal keys:
      _bar_idx  — index into bars list
      _open     — next bar's open (fill price for trades)

    Returns [] if fewer than _MIN_BARS bars.
    The last bar is always excluded (no next bar to fill at).
    """
    if len(bars) < _MIN_BARS:
        return []

    closes = pd.Series([float(b["c"]) for b in bars])
    volumes = pd.Series([float(b["v"]) for b in bars])

    rsi_series = ta.momentum.RSIIndicator(close=closes, window=14).rsi()
    sma20_series = ta.trend.SMAIndicator(close=closes, window=20).sma_indicator()
    sma50_series = ta.trend.SMAIndicator(close=closes, window=50).sma_indicator()
    vol_avg_series = volumes.rolling(window=20).mean()

    snapshots = []
    # SMA50 first valid at index 49. sma20_prev2 needs sma20[i-2], valid from i=21.
    # Binding constraint: i >= 49. Stop at len(bars)-2 so bars[i+1] always exists.
    for i in range(49, len(bars) - 1):
        if (
            pd.isna(rsi_series.iloc[i])
            or pd.isna(sma20_series.iloc[i])
            or pd.isna(sma50_series.iloc[i])
            or i < 2
            or pd.isna(sma20_series.iloc[i - 1])
            or pd.isna(sma20_series.iloc[i - 2])
            or pd.isna(vol_avg_series.iloc[i])
        ):
            continue
        snapshots.append({
            "price":       float(closes.iloc[i]),
            "rsi":         float(rsi_series.iloc[i]),
            "sma20":       float(sma20_series.iloc[i]),
            "sma50":       float(sma50_series.iloc[i]),
            "sma20_prev":  float(sma20_series.iloc[i - 1]),
            "sma20_prev2": float(sma20_series.iloc[i - 2]),
            "volume":      float(volumes.iloc[i]),
            "volume_avg":  float(vol_avg_series.iloc[i]),
            "_bar_idx":    i,
            "_open":       float(bars[i + 1]["o"]),
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
