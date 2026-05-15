import time
from typing import Optional
import requests
import alpaca_trade_api as tradeapi
from shared.db import get_conn
from shared.logger import get_logger

log = get_logger("data")


def write_health_result(api_name: str, status: str, latency_ms: int, error_message: Optional[str]) -> None:
    sql = "INSERT INTO api_health (api_name, status, latency_ms, error_message) VALUES (%s, %s, %s, %s)"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (api_name, status, latency_ms, error_message))


def check_all(alpaca_key, alpaca_secret, alpaca_base_url, finnhub_token, fred_api_key) -> dict:
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
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": "AAPL", "from": "2026-01-01", "to": "2026-01-02", "token": finnhub_token},
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

    return results
