"""
watchdog.py — AlphaDivision host-level service watchdog.

Runs outside Docker on the VM host. Monitors service heartbeats via Redis,
auto-restarts failed services via docker compose, and sends Discord/email alerts.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any

import redis
import requests
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SERVICES = ["data", "analysis", "execution", "alerts", "dashboard"]
DASHBOARD_HEALTH_URL = "http://localhost:8080/health"
MAX_RESTARTS = 3
POLL_INTERVAL = 120       # seconds between watchdog cycles
RESTART_WINDOW = 3600     # TTL for restart count key (1 hour)
ALERT_WINDOW = 3600       # TTL for alert state key (1 hour)

# Path to docker-compose project directory
COMPOSE_DIR = os.environ.get("COMPOSE_DIR", "/opt/alphadivision")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("watchdog")

# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

def send_discord(webhook_url: str, message: str) -> None:
    """POST a message to a Discord webhook. Raises on failure."""
    resp = requests.post(webhook_url, json={"content": message}, timeout=10)
    resp.raise_for_status()


def send_email(
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    """Send a plain-text email via SendGrid. Raises on failure."""
    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body,
    )
    sg = SendGridAPIClient(api_key)
    sg.send(message)


# ---------------------------------------------------------------------------
# Redis state helpers
# ---------------------------------------------------------------------------

def get_heartbeat_ttl(r: redis.Redis, service: str) -> int:
    """Return TTL of heartbeat key. <= 0 means service is considered down."""
    return r.ttl(f"heartbeat:{service}")


def get_alert_state(r: redis.Redis, service: str) -> str | None:
    """Return alert state string ('alerted', 'critical') or None."""
    val = r.get(f"watchdog:alerted:{service}")
    if val is None:
        return None
    return val.decode() if isinstance(val, bytes) else val


def set_alert_state(r: redis.Redis, service: str, state: str) -> None:
    """Persist alert state with a 1-hour TTL."""
    r.setex(f"watchdog:alerted:{service}", ALERT_WINDOW, state)


def clear_service_state(r: redis.Redis, service: str) -> None:
    """Delete alert state and restart count keys for a service."""
    r.delete(f"watchdog:alerted:{service}")
    r.delete(f"watchdog:restarts:{service}")


def get_restart_count(r: redis.Redis, service: str) -> int:
    """Return current restart attempt count (0 if key does not exist)."""
    val = r.get(f"watchdog:restarts:{service}")
    if val is None:
        return 0
    return int(val)


def increment_restart_count(r: redis.Redis, service: str) -> int:
    """Increment restart count; set 1-hour TTL on first increment. Returns new count."""
    new_count = r.incr(f"watchdog:restarts:{service}")
    if new_count == 1:
        r.expire(f"watchdog:restarts:{service}", RESTART_WINDOW)
    return new_count


# ---------------------------------------------------------------------------
# Service restart
# ---------------------------------------------------------------------------

def restart_service(service: str) -> bool:
    """
    Run `docker compose restart <service>` in COMPOSE_DIR.
    Returns True on success, False on failure.
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "restart", service],
            cwd=COMPOSE_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.error("docker compose restart %s failed: %s", service, result.stderr)
            return False
        log.info("Restarted service: %s", service)
        return True
    except Exception as exc:
        log.error("Exception restarting %s: %s", service, exc)
        return False
