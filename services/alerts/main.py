import sys
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app")

from shared.logger import get_logger
from shared.redis_client import get_redis

from notifier import send_discord, send_email
from trade_watcher import check_new_trades
from circuit_breaker_watcher import check_circuit_breaker
from crash_watcher import SERVICES, check_service

log = get_logger("alerts")

_ET = ZoneInfo("America/New_York")
_HEARTBEAT_KEY = "heartbeat:alerts"
_HEARTBEAT_TTL = 90
_HEARTBEAT_INTERVAL = 60   # seconds between heartbeat publishes
_POLL_INTERVAL = 30        # seconds between alert checks


def _get_env(key: str) -> str:
    """Retrieve a required environment variable, raising clearly if missing."""
    value = os.getenv(key, "")
    if not value:
        raise RuntimeError(f"Required environment variable '{key}' is not set")
    return value


def _publish_heartbeat() -> None:
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


def main() -> None:
    log.info("Alerts Service starting")

    webhook_url = _get_env("DISCORD_WEBHOOK_URL")
    sg_api_key = _get_env("SENDGRID_API_KEY")
    email_to = _get_env("ALERT_EMAIL_TO")
    email_from = _get_env("ALERT_EMAIL_FROM")

    last_heartbeat = 0.0

    while True:
        now = time.time()

        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            try:
                _publish_heartbeat()
                last_heartbeat = now
            except Exception as exc:
                log.error("Heartbeat failed: %s", exc)

        today = datetime.now(_ET).date()

        try:
            check_new_trades(webhook_url, send_discord)
        except Exception as exc:
            log.error("Trade watcher error: %s", exc, exc_info=True)

        try:
            check_circuit_breaker(
                today=today,
                webhook_url=webhook_url,
                send_discord_fn=send_discord,
                send_email_fn=send_email,
                email_from=email_from,
                email_to=email_to,
                sg_api_key=sg_api_key,
            )
        except Exception as exc:
            log.error("Circuit breaker watcher error: %s", exc, exc_info=True)

        for service in SERVICES:
            try:
                check_service(
                    service=service,
                    webhook_url=webhook_url,
                    send_discord_fn=send_discord,
                    send_email_fn=send_email,
                    email_from=email_from,
                    email_to=email_to,
                    sg_api_key=sg_api_key,
                )
            except Exception as exc:
                log.error("Crash watcher error for %s: %s", service, exc, exc_info=True)

        time.sleep(_POLL_INTERVAL)


if __name__ == "__main__":
    main()
