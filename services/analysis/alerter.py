import os
import sys

sys.path.insert(0, "/app")

import requests
from shared.logger import get_logger

log = get_logger("analysis.alerter")

_WEBHOOK_URL_ENV = "DISCORD_WEBHOOK_URL"


def send_alert(message: str) -> None:
    """Post message to Discord webhook. Falls back to log.warning if URL is not configured."""
    webhook_url = os.getenv(_WEBHOOK_URL_ENV, "").strip()

    if not webhook_url:
        log.warning(f"[alert] Discord webhook not configured — alert suppressed: {message}")
        return

    try:
        response = requests.post(webhook_url, json={"content": message}, timeout=5)
        response.raise_for_status()
    except Exception as exc:
        log.error(f"[alert] Failed to post Discord alert: {exc}")
