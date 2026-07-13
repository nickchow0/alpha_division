import logging
import os
import requests

log = logging.getLogger("watchdog.notifier")


def send_notification(message: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        log.warning("DISCORD_WEBHOOK_URL not set — skipping notification")
        return
    try:
        requests.post(url, json={"content": message}, timeout=10)
    except Exception as exc:
        log.error(f"Discord notification failed: {exc}")
