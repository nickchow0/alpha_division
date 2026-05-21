import sys
import os
import time
import threading
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from shared.logger import get_logger
from shared.redis_client import get_redis
from market import is_market_open, get_watchlist
from fetchers import fetch_bars, fetch_news, fetch_macro, fetch_account_equity, fetch_latest_price
from indicators import calculate_indicators
from publisher import publish_snapshot, publish_heartbeat, publish_account_equity
from health_checker import check_all, check_ai_api, write_health_result

log = get_logger("data")

# Scheduling intervals (seconds)
_PRICE_INTERVAL = 15 * 60       # 15 minutes
_NEWS_INTERVAL = 60 * 60        # 60 minutes
_MACRO_INTERVAL = 24 * 60 * 60  # 24 hours
_HEALTH_INTERVAL = 5 * 60       # 5 minutes
_HEARTBEAT_INTERVAL = 60        # 60 seconds

_REDIS_AI_PROVIDER_KEY = "config:ai_provider"
_REDIS_AI_CHECK_TRIGGER = "health:ai_check_requested"


def _get_env(key: str, required: bool = True) -> str:
    """Retrieve a required environment variable, raising clearly if missing."""
    value = os.getenv(key, "")
    if required and not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set")
    return value


def _health_loop(alpaca_key: str, alpaca_secret: str, alpaca_base_url: str,
                 finnhub_token: str, fred_api_key: str,
                 anthropic_api_key: str = "", gemini_api_key: str = "") -> None:
    """Daemon thread: run API health checks every 5 minutes."""
    while True:
        try:
            results = check_all(alpaca_key, alpaca_secret, alpaca_base_url,
                                finnhub_token, fred_api_key,
                                anthropic_api_key=anthropic_api_key,
                                gemini_api_key=gemini_api_key)
            log.info(f"Health check results: {results}")
        except Exception as exc:
            log.error(f"Health check loop error: {exc}")
        time.sleep(_HEALTH_INTERVAL)


def _fetch_and_publish_price(symbol: str, alpaca_key: str, alpaca_secret: str,
                              alpaca_base_url: str, cached_news: list,
                              cached_macro: dict) -> None:
    """
    Fetch bars + calculate indicators for one symbol, then publish a snapshot.
    Any exception is caught and logged — the calling loop continues.
    """
    try:
        bars = fetch_bars(symbol, alpaca_key, alpaca_secret, alpaca_base_url)
        indicators = calculate_indicators(bars)
        if indicators is None:
            log.warning(f"Not enough bars to calculate indicators for {symbol} (got {len(bars)})")
            return

        # Use live last-trade price; fall back to previous daily close if unavailable
        try:
            price = fetch_latest_price(symbol, alpaca_key, alpaca_secret, alpaca_base_url)
        except Exception as exc:
            price = bars[-1]["c"]
            log.warning(f"[{symbol}] Live price fetch failed ({exc}), using last bar close {price:.2f}")

        snapshot = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price": price,
            "rsi": indicators["rsi"],
            "sma20": indicators["sma20"],
            "sma50": indicators["sma50"],
            "sma20_prev": indicators["sma20_prev"],
            "sma20_prev2": indicators["sma20_prev2"],
            "news": cached_news,
            "macro": cached_macro,
        }
        publish_snapshot(snapshot)
        log.info(f"Published snapshot for {symbol}: price={price:.2f} rsi={indicators['rsi']:.1f}")
    except Exception as exc:
        log.error(f"Error fetching/publishing price for {symbol}: {exc}")


def main() -> None:
    log.info("Data Service starting")

    # Load required configuration from environment
    alpaca_key = _get_env("ALPACA_API_KEY")
    alpaca_secret = _get_env("ALPACA_SECRET_KEY")
    alpaca_base_url = _get_env("ALPACA_BASE_URL")
    finnhub_token = _get_env("FINNHUB_API_KEY")
    fred_api_key = _get_env("FRED_API_KEY")
    anthropic_api_key = _get_env("ANTHROPIC_API_KEY")
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")

    # Start health-check daemon thread
    health_thread = threading.Thread(
        target=_health_loop,
        args=(alpaca_key, alpaca_secret, alpaca_base_url, finnhub_token, fred_api_key),
        kwargs={"anthropic_api_key": anthropic_api_key, "gemini_api_key": gemini_api_key},
        daemon=True,
        name="health-checker",
    )
    health_thread.start()
    log.info("Health checker thread started")

    # Per-symbol caches reused between price cycles
    symbol_news_cache: dict = {}
    cached_macro: dict = {}

    # Timestamps tracking the last time each job ran (0 = never)
    last_price: float = 0.0
    last_news: float = 0.0
    last_macro: float = 0.0
    last_heartbeat: float = 0.0

    watchlist = get_watchlist()
    log.info(f"Watchlist: {watchlist}")

    while True:
        now = time.time()

        # --- Heartbeat (every 60s) ---
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            try:
                publish_heartbeat()
            except Exception as exc:
                log.error(f"Heartbeat publish failed: {exc}")
            last_heartbeat = now

        # --- Immediate AI health check (triggered by provider switch in dashboard) ---
        try:
            r = get_redis()
            if r.getdel(_REDIS_AI_CHECK_TRIGGER):
                raw = r.get(_REDIS_AI_PROVIDER_KEY)
                provider = (raw.decode() if isinstance(raw, bytes) else raw) if raw else "claude"
                ai_key = gemini_api_key if provider == "gemini" else anthropic_api_key
                log.info(f"Immediate AI health check triggered for provider: {provider}")
                try:
                    import time as _time
                    _start = _time.monotonic()
                    check_ai_api(provider, ai_key)
                    latency_ms = int((_time.monotonic() - _start) * 1000)
                    write_health_result(provider, "ok", latency_ms, None)
                    log.info(f"Immediate AI health check passed: {provider} ({latency_ms}ms)")
                except Exception as exc:
                    write_health_result(provider, "warning", 0, str(exc))
                    log.warning(f"Immediate AI health check failed for {provider}: {exc}")
        except Exception as exc:
            log.error(f"AI health trigger check error: {exc}")

        # --- Macro data (once per day) ---
        if now - last_macro >= _MACRO_INTERVAL:
            try:
                cached_macro = fetch_macro(fred_api_key)
                log.info(f"Fetched macro data: {cached_macro}")
            except Exception as exc:
                log.error(f"Macro fetch failed: {exc}")
            last_macro = now

        # --- News (every 60 min) ---
        if now - last_news >= _NEWS_INTERVAL:
            for symbol in watchlist:
                try:
                    symbol_news_cache[symbol] = fetch_news(symbol, finnhub_token)
                    log.info(f"Fetched {len(symbol_news_cache[symbol])} news items for {symbol}")
                except Exception as exc:
                    log.error(f"News fetch failed for {symbol}: {exc}")
                    symbol_news_cache.setdefault(symbol, [])
            last_news = now

        # --- Price + indicators + account equity (every 15 min, market hours only) ---
        if now - last_price >= _PRICE_INTERVAL and is_market_open():
            for symbol in watchlist:
                _fetch_and_publish_price(
                    symbol,
                    alpaca_key,
                    alpaca_secret,
                    alpaca_base_url,
                    symbol_news_cache.get(symbol, []),
                    cached_macro,
                )
            try:
                equity = fetch_account_equity(alpaca_key, alpaca_secret, alpaca_base_url)
                publish_account_equity(equity)
                log.info(f"Published account equity: ${equity:,.2f}")
            except Exception as exc:
                log.error(f"Account equity fetch failed: {exc}")
            last_price = now

        time.sleep(5)


if __name__ == "__main__":
    main()
