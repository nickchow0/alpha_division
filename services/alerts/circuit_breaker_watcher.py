from datetime import date as Date

from shared.db import get_conn
from shared.redis_client import get_redis
from shared.logger import get_logger

log = get_logger("circuit_breaker_watcher")


def _cb_alerted_key(today: Date) -> str:
    return f"alert:cb_alerted:{today.isoformat()}"


def is_cb_alerted_today(today: Date) -> bool:
    """Return True if we've already sent a circuit-breaker alert today."""
    r = get_redis()
    return r.get(_cb_alerted_key(today)) is not None


def mark_cb_alerted(today: Date) -> None:
    """Mark that a circuit-breaker alert was sent today (TTL 24 h)."""
    r = get_redis()
    r.setex(_cb_alerted_key(today), 86400, "1")


def is_circuit_breaker_triggered(today: Date) -> bool:
    """Return True if today's daily_pnl row has circuit_breaker_triggered = True."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT circuit_breaker_triggered FROM daily_pnl WHERE date = %s",
                (today,),
            )
            row = cur.fetchone()
    if row is None:
        return False
    return bool(row["circuit_breaker_triggered"])


def check_circuit_breaker(
    today: Date,
    webhook_url: str,
    send_discord_fn,
    send_email_fn,
    email_from: str,
    email_to: str,
    sg_api_key: str,
) -> None:
    """Send Discord + email if the circuit breaker fired today and we haven't alerted yet."""
    if is_cb_alerted_today(today) or not is_circuit_breaker_triggered(today):
        return

    subject = f"🚨 AlphaDivision: Circuit Breaker Triggered — {today.isoformat()}"
    body = (
        f"The daily loss circuit breaker was triggered on {today.isoformat()}.\n"
        "Trading has been halted for the remainder of the day."
    )

    try:
        send_discord_fn(webhook_url, f"🚨 **Circuit breaker triggered** — {today.isoformat()}. Trading halted.")
    except Exception as exc:
        log.error("Failed to send Discord circuit-breaker alert: %s", exc)

    try:
        send_email_fn(sg_api_key, email_from, email_to, subject, body)
    except Exception as exc:
        log.error("Failed to send email circuit-breaker alert: %s", exc)

    mark_cb_alerted(today)
