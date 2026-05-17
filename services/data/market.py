import os
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MINUTE = 30
_MARKET_CLOSE_HOUR = 16
_MARKET_CLOSE_MINUTE = 0


def is_market_open() -> bool:
    """Return True if NYSE trading hours are active (9:30am–4:00pm ET, Mon–Fri).

    NOTE: Does not account for NYSE market holidays. The Analysis Service will
    run on holidays — this is an acceptable V1 limitation since the bot uses
    paper trading and no trades execute if Alpaca has no market data to return.
    """
    now = datetime.now(ET)
    # weekday(): 0=Monday … 4=Friday, 5=Saturday, 6=Sunday
    if now.weekday() >= 5:
        return False
    open_time = now.replace(hour=_MARKET_OPEN_HOUR, minute=_MARKET_OPEN_MINUTE, second=0, microsecond=0)
    close_time = now.replace(hour=_MARKET_CLOSE_HOUR, minute=_MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return open_time <= now < close_time


_DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOGL"]


def get_watchlist() -> list[str]:
    """Return the list of symbols from watchlist.txt (one symbol per line).

    Falls back to the WATCHLIST env var (comma-separated) for backward
    compatibility, then to a hardcoded default if neither is set.
    """
    try:
        path = os.environ.get("WATCHLIST_FILE", "/app/watchlist.txt")
        with open(path) as f:
            symbols = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
        if symbols:
            return symbols
    except FileNotFoundError:
        pass

    # Fallback: WATCHLIST env var
    raw = os.getenv("WATCHLIST", "")
    if raw.strip():
        return [s.strip() for s in raw.split(",") if s.strip()]

    return _DEFAULT_WATCHLIST
