import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests
import alpaca_trade_api as tradeapi
import anthropic
import google.generativeai as genai
from shared.db import get_conn
from shared.redis_client import get_redis
from shared.logger import get_logger

_REDIS_AI_PROVIDER_KEY = "config:ai_provider"

log = get_logger("data")


def write_health_result(api_name: str, status: str, latency_ms: int, error_message: Optional[str]) -> None:
    sql = "INSERT INTO api_health (api_name, status, latency_ms, error_message) VALUES (%s, %s, %s, %s)"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (api_name, status, latency_ms, error_message))


def check_ai_api(provider: str, api_key: str, model: str = "") -> str:
    """
    Run a minimal liveness check against the active AI provider.

    Makes the smallest possible real API call to verify the key is valid and
    the service is reachable. Returns "ok" on success, raises on failure.
    """
    if provider == "claude":
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model=model or "claude-haiku-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": "Reply OK."}],
        )
        return "ok"
    if provider == "gemini":
        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(model or "gemini-2.5-flash")
        m.generate_content("Reply OK.")
        return "ok"
    raise ValueError(f"Unknown AI provider '{provider}'")


def check_all(alpaca_key, alpaca_secret, alpaca_base_url, finnhub_token, fred_api_key,
              anthropic_api_key: str = "", gemini_api_key: str = "",
              check_ai: bool = True) -> dict:
    results = {}

    # Alpaca (critical — "error" on failure)
    try:
        start = time.monotonic()
        api = tradeapi.REST(alpaca_key, alpaca_secret, alpaca_base_url)
        bars_resp = api.get_bars("AAPL", "1Day", limit=1)
        _ = bars_resp.df
        latency_ms = int((time.monotonic() - start) * 1000)
        write_health_result("alpaca", "ok", latency_ms, None)
        results["alpaca"] = "ok"
    except Exception as exc:
        log.error(f"Alpaca health check failed: {exc}")
        write_health_result("alpaca", "error", 0, str(exc))
        results["alpaca"] = "error"

    # Finnhub (non-critical — "warning" on failure)
    try:
        start = time.monotonic()
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": "AAPL",
                "from": yesterday.strftime("%Y-%m-%d"),
                "to": now.strftime("%Y-%m-%d"),
                "token": finnhub_token,
            },
            timeout=10,
        )
        resp.raise_for_status()
        latency_ms = int((time.monotonic() - start) * 1000)
        write_health_result("finnhub", "ok", latency_ms, None)
        results["finnhub"] = "ok"
    except Exception as exc:
        log.warning(f"Finnhub health check failed: {exc}")
        write_health_result("finnhub", "warning", 0, str(exc))
        results["finnhub"] = "warning"

    # FRED (non-critical — "warning" on failure)
    try:
        start = time.monotonic()
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": "FEDFUNDS", "api_key": fred_api_key, "file_type": "json", "sort_order": "desc", "limit": 1},
            timeout=10,
        )
        resp.raise_for_status()
        latency_ms = int((time.monotonic() - start) * 1000)
        write_health_result("fred", "ok", latency_ms, None)
        results["fred"] = "ok"
    except Exception as exc:
        log.warning(f"FRED health check failed: {exc}")
        write_health_result("fred", "warning", 0, str(exc))
        results["fred"] = "warning"

    # AI provider (non-critical — "warning" on failure, hourly cadence)
    if check_ai:
        r = get_redis()
        raw = r.get(_REDIS_AI_PROVIDER_KEY)
        provider = (raw.decode() if isinstance(raw, bytes) else raw) if raw else "claude"
        model_key = "config:gemini_model" if provider == "gemini" else "config:claude_model"
        raw_model = r.get(model_key)
        model = (raw_model.decode() if isinstance(raw_model, bytes) else raw_model) if raw_model else ""
        ai_key = gemini_api_key if provider == "gemini" else anthropic_api_key
        try:
            start = time.monotonic()
            check_ai_api(provider, ai_key, model=model)
            latency_ms = int((time.monotonic() - start) * 1000)
            write_health_result(provider, "ok", latency_ms, None)
            results[provider] = "ok"
        except Exception as exc:
            log.warning(f"{provider.capitalize()} health check failed: {exc}")
            write_health_result(provider, "warning", 0, str(exc))
            results[provider] = "warning"

    return results
