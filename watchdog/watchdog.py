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


# ---------------------------------------------------------------------------
# Dashboard HTTP health check
# ---------------------------------------------------------------------------

def check_dashboard_health(webhook_url: str) -> None:
    """
    GET the dashboard /health endpoint. Send a Discord alert if non-200 or unreachable.
    Email is intentionally omitted — this is a soft health check, not a service-down alert.
    """
    try:
        resp = requests.get(DASHBOARD_HEALTH_URL, timeout=10)
        if resp.status_code != 200:
            log.warning("Dashboard /health returned HTTP %s", resp.status_code)
            try:
                send_discord(
                    webhook_url,
                    f"⚠️ Dashboard /health returned HTTP {resp.status_code}",
                )
            except Exception as exc:
                log.error("Failed to send Discord dashboard health alert: %s", exc)
    except Exception as exc:
        log.warning("Dashboard /health unreachable: %s", exc)
        try:
            send_discord(
                webhook_url,
                f"⚠️ Dashboard /health unreachable: {exc}",
            )
        except Exception as disc_exc:
            log.error("Failed to send Discord dashboard health alert: %s", disc_exc)


# ---------------------------------------------------------------------------
# Per-service state machine
# ---------------------------------------------------------------------------

def check_service(r: redis.Redis, service: str, cfg: dict[str, Any]) -> None:
    """
    Evaluate a single service's state and take appropriate action.

    State machine:
      UP  + alerted          → recovery Discord + clear state
      UP  + not alerted      → no-op
      DOWN + count >= 3 + not critical → CRITICAL Discord + email, set critical
      DOWN + count >= 3 + critical     → no-op
      DOWN + count < 3  + not alerted  → normal Discord + email, set alerted, restart, incr
      DOWN + count < 3  + alerted      → restart only, incr
    """
    ttl = get_heartbeat_ttl(r, service)
    is_up = ttl > 0
    alert_state = get_alert_state(r, service)
    restart_count = get_restart_count(r, service)

    webhook_url = cfg["webhook_url"]
    sg_api_key = cfg["sg_api_key"]
    email_from = cfg["email_from"]
    email_to = cfg["email_to"]

    if is_up:
        if alert_state is not None:
            # Branch 1: Service recovered
            log.info("Service %s recovered (was %s)", service, alert_state)
            try:
                send_discord(webhook_url, f"✅ **{service}** has recovered.")
            except Exception as exc:
                log.error("Failed to send recovery alert for %s: %s", service, exc)
            clear_service_state(r, service)
        else:
            # Branch 2: Healthy, nothing to do
            log.debug("Service %s is healthy (TTL=%d)", service, ttl)
        return

    # Service is DOWN
    log.warning("Service %s is DOWN (TTL=%d, restarts=%d, state=%s)", service, ttl, restart_count, alert_state)

    if restart_count >= MAX_RESTARTS:
        if alert_state == "critical":
            # Branch 4: Already escalated, wait for key expiry
            log.debug("Service %s still critical — awaiting key expiry", service)
        else:
            # Branch 3: Escalate to CRITICAL
            msg = (
                f"🚨 **CRITICAL: {service} is DOWN** — "
                f"{restart_count} restart attempts exhausted. Manual intervention required."
            )
            try:
                send_discord(webhook_url, msg)
            except Exception as exc:
                log.error("Failed to send critical Discord alert for %s: %s", service, exc)
            try:
                send_email(
                    sg_api_key,
                    email_from,
                    email_to,
                    f"CRITICAL: AlphaDivision {service} is DOWN",
                    f"Service {service} is DOWN after {restart_count} restart attempts. "
                    "Manual intervention required.",
                )
            except Exception as exc:
                log.error("Failed to send critical email for %s: %s", service, exc)
            set_alert_state(r, service, "critical")
        return

    # restart_count < MAX_RESTARTS — attempt restart
    if alert_state is None:
        # Branch 5: First detection — alert then restart
        msg = f"⚠️ **{service} is DOWN** — attempting restart ({restart_count + 1}/{MAX_RESTARTS})."
        try:
            send_discord(webhook_url, msg)
        except Exception as exc:
            log.error("Failed to send Discord alert for %s: %s", service, exc)
        try:
            send_email(
                sg_api_key,
                email_from,
                email_to,
                f"AlphaDivision {service} is DOWN",
                f"Service {service} is DOWN. Attempting restart "
                f"({restart_count + 1}/{MAX_RESTARTS}).",
            )
        except Exception as exc:
            log.error("Failed to send email alert for %s: %s", service, exc)
        set_alert_state(r, service, "alerted")
    else:
        # Branch 6: Already alerted — restart without duplicate notification
        log.info("Service %s still down — retrying restart (%d/%d)", service, restart_count + 1, MAX_RESTARTS)

    if not restart_service(service):
        log.warning("Restart command failed for %s (attempt %d/%d)", service, restart_count + 1, MAX_RESTARTS)
    increment_restart_count(r, service)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_cycle(r: redis.Redis, cfg: dict[str, Any]) -> None:
    """Run one watchdog cycle: check all services then check dashboard HTTP health."""
    log.info("watchdog alive")
    for service in SERVICES:
        try:
            check_service(r, service, cfg)
        except Exception as exc:
            log.error("Unexpected error checking service %s: %s", service, exc, exc_info=True)
    check_dashboard_health(cfg["webhook_url"])


def main() -> None:
    """Entry point: load config, connect to Redis, run forever."""
    # Load .env from /opt/alphadivision/.env if it exists, else fall back to CWD
    env_path = "/opt/alphadivision/.env"
    if not os.path.exists(env_path):
        env_path = ".env"
    load_dotenv(env_path)

    required_vars = ["REDIS_URL", "DISCORD_WEBHOOK_URL", "SENDGRID_API_KEY", "ALERT_EMAIL_FROM", "ALERT_EMAIL_TO"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        log.critical("Missing required environment variables: %s", ", ".join(missing))
        raise SystemExit(1)

    redis_url = os.environ["REDIS_URL"]
    cfg: dict[str, Any] = {
        "webhook_url": os.environ["DISCORD_WEBHOOK_URL"],
        "sg_api_key": os.environ["SENDGRID_API_KEY"],
        "email_from": os.environ["ALERT_EMAIL_FROM"],
        "email_to": os.environ["ALERT_EMAIL_TO"],
    }

    # decode_responses=False: get_alert_state manually decodes bytes; changing this to True
    # would silently break the isinstance branch in get_alert_state.
    r = redis.from_url(redis_url, decode_responses=False)

    log.info("Watchdog started. Monitoring: %s", ", ".join(SERVICES))

    while True:
        try:
            run_cycle(r, cfg)
        except Exception as exc:
            log.error("Unhandled error in watchdog cycle: %s", exc, exc_info=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
