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
    Layer 1a position rules for all four order sides.

    - buy:   reject if symbol has any position (long or short) — prevents mixing
    - sell:  reject if symbol is not currently long (qty > 0)
    - short: reject if symbol has any position (long or short)
    - cover: reject if symbol is not currently short (qty < 0)

    Parameters:
        symbol: ticker symbol (e.g. "AAPL")
        side: "buy", "sell", "short", or "cover"
        positions: dict mapping symbol -> qty (negative qty = short position)
    """
    if side in ("buy", "short"):
        if symbol in positions:
            qty = positions[symbol]
            direction = "long" if qty > 0 else "short"
            return (
                False,
                f"Cannot {side} {symbol} — already have a {direction} position ({qty} shares)",
            )
        return True, ""

    if side == "sell":
        if symbol not in positions or positions[symbol] <= 0:
            return False, f"Cannot sell {symbol} — no long position held"
        return True, ""

    if side == "cover":
        if symbol not in positions or positions[symbol] >= 0:
            return False, f"Cannot cover {symbol} — no short position held"
        return True, ""

    return False, f"Unknown order side: {side!r}"


def check_position_limit(
    positions: dict,
    side: str = "buy",
    max_positions: int = 10,
    max_short_positions: int = 5,
) -> Tuple[bool, str]:
    """
    Layer 1b: enforce maximum open positions per direction.

    - buy:   count long positions (qty > 0), enforce max_positions
    - short: count short positions (qty < 0), enforce max_short_positions
    - sell or cover: always allowed regardless of count

    Parameters:
        positions: dict mapping symbol -> qty (negative = short)
        side: "buy", "sell", "short", or "cover"
        max_positions: maximum concurrent long positions
        max_short_positions: maximum concurrent short positions
    """
    if side in ("sell", "cover"):
        return True, ""

    if side == "buy":
        count = sum(1 for qty in positions.values() if qty > 0)
        if count >= max_positions:
            return False, f"At maximum long positions ({count}/{max_positions})"
        return True, ""

    if side == "short":
        count = sum(1 for qty in positions.values() if qty < 0)
        if count >= max_short_positions:
            return False, f"At maximum short positions ({count}/{max_short_positions})"
        return True, ""

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
