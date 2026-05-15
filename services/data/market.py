import os
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MINUTE = 30
_MARKET_CLOSE_HOUR = 16
_MARKET_CLOSE_MINUTE = 0


def is_market_open() -> bool:
    """Return True if the NYSE is currently open (9:30am–4:00pm ET, Mon–Fri)."""
    now = datetime.now(ET)
    # weekday(): 0=Monday … 4=Friday, 5=Saturday, 6=Sunday
    if now.weekday() >= 5:
        return False
    open_time = now.replace(hour=_MARKET_OPEN_HOUR, minute=_MARKET_OPEN_MINUTE, second=0, microsecond=0)
    close_time = now.replace(hour=_MARKET_CLOSE_HOUR, minute=_MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return open_time <= now < close_time


def get_watchlist() -> list[str]:
    """Return the list of symbols from the WATCHLIST env var, or the default."""
    raw = os.getenv("WATCHLIST", "AAPL,MSFT,GOOGL")
    return [s.strip() for s in raw.split(",") if s.strip()]
