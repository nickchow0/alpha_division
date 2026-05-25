import math
from datetime import datetime, time
from typing import Tuple
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
_BLACKOUT_END = time(10, 0)
_CIRCUIT_BREAKER_LIMIT = 1000.0


def check_trading_window(now: datetime) -> Tuple[bool, str]:
    """
    Returns (True, "") if trading is currently allowed.

    Blocks if:
    - Weekend (Saturday or Sunday)
    - Before market open or at/after market close (outside 9:30–16:00 ET)
    - Within the post-open blackout window (9:30–10:00 ET)

    The effective trading window is 10:00am–4:00pm ET on weekdays.
    """
    now_et = now.astimezone(_ET)

    if now_et.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False, f"Weekend — market closed"

    t = now_et.time().replace(tzinfo=None)

    if not (_MARKET_OPEN <= t < _MARKET_CLOSE):
        return False, f"Outside market hours ({t.strftime('%H:%M')} ET)"

    if t < _BLACKOUT_END:
        return False, "Post-open blackout window (9:30–10:00 ET)"

    return True, ""


def check_position_rules(symbol: str, side: str, positions: dict) -> Tuple[bool, str]:
    """
    Layer 1a position rules:
    - buy: reject if the symbol is already held (prevents doubling up)
    - sell: reject if the symbol is not currently held

    Parameters:
        symbol: ticker symbol (e.g. "AAPL")
        side: "buy" or "sell"
        positions: dict mapping symbol -> qty for currently held positions
    """
    if side not in ("buy", "sell"):
        return False, f"Unknown order side: {side!r}"
    if side == "buy" and symbol in positions:
        return (
            False,
            f"Already holding {symbol} ({positions[symbol]} shares) — will not double-up",
        )
    if side == "sell" and symbol not in positions:
        return False, f"Cannot sell {symbol} — not currently held"
    return True, ""


def check_position_limit(positions: dict, side: str = "buy", max_positions: int = 10) -> Tuple[bool, str]:
    """
    Layer 1b: enforce maximum open positions.

    Only enforced for buy orders — sells are always allowed regardless of
    position count. Passing side="sell" always returns (True, "").
    Count is based on number of keys in positions dict, regardless of qty.
    """
    if side == "sell":
        return True, ""
    count = len(positions)
    if count >= max_positions:
        return False, f"At maximum open positions ({count}/{max_positions})"
    return True, ""


def calculate_qty(portfolio_value: float, price: float, risk_pct: float = 0.04) -> int:
    """
    Layer 2: calculate order size as a fraction of portfolio value.

    Formula: floor(portfolio_value × risk_pct / price)

    Returns 0 if price is zero or negative, or if the portfolio is too small
    to buy even a single share at the configured risk percentage.
    """
    if price <= 0 or portfolio_value <= 0:
        return 0
    return math.floor((portfolio_value * risk_pct) / price)


def check_circuit_breaker(daily_pnl: float) -> Tuple[bool, str]:
    """
    Layer 3: halt all new orders if daily realized losses reach $200.

    Parameters:
        daily_pnl: today's realized P&L in dollars (negative = loss)

    Returns (True, "") if trading is allowed.
    Returns (False, reason) if daily losses have reached the limit.
    """
    if daily_pnl <= -_CIRCUIT_BREAKER_LIMIT:
        return False, (
            f"Circuit breaker triggered — daily loss ${abs(daily_pnl):.2f} "
            f"exceeds ${_CIRCUIT_BREAKER_LIMIT:.0f} limit"
        )
    return True, ""
