from shared.redis_client import get_redis
from shared.logger import get_logger

log = get_logger("crash_watcher")

SERVICES = ["data", "analysis", "execution"]


def is_service_alive(service: str) -> bool:
    """Return True if the service heartbeat key exists in Redis."""
    r = get_redis()
    return bool(r.exists(f"heartbeat:{service}"))


def is_crash_suspected(service: str) -> bool:
    """Return True if the first-miss suspect flag is set."""
    r = get_redis()
    return bool(r.exists(f"alert:crash_suspect:{service}"))


def is_crash_alerted(service: str) -> bool:
    """Return True if a crash alert has already been sent."""
    r = get_redis()
    return bool(r.exists(f"alert:crash:{service}"))


def mark_crash_suspected(service: str) -> None:
    """Set the suspect flag (first consecutive miss). TTL 90 s."""
    r = get_redis()
    r.setex(f"alert:crash_suspect:{service}", 90, "1")


def mark_crash_alerted(service: str) -> None:
    """Set the crash-alerted flag. TTL 1 h."""
    r = get_redis()
    r.setex(f"alert:crash:{service}", 3600, "1")


def clear_crash_state(service: str) -> None:
    """Delete both crash and suspect keys (service recovered)."""
    r = get_redis()
    r.delete(f"alert:crash:{service}")
    r.delete(f"alert:crash_suspect:{service}")


def check_service(
    service: str,
    webhook_url: str,
    send_discord_fn,
    send_email_fn,
    email_from: str,
    email_to: str,
    sg_api_key: str,
) -> None:
    """Run one crash-detection cycle for a single service."""
    alive = is_service_alive(service)

    if alive:
        if is_crash_alerted(service):
            # Service has recovered from a crash
            try:
                send_discord_fn(
                    webhook_url,
                    f"✅ **{service} service recovered** — heartbeat restored.",
                )
            except Exception as exc:
                log.error("Failed to send recovery Discord for %s: %s", service, exc)
            clear_crash_state(service)
        return

    # Service is dead
    if is_crash_alerted(service):
        # Already alerted, don't repeat
        return

    if is_crash_suspected(service):
        # Second consecutive miss — send crash alert
        subject = f"🚨 AlphaDivision: {service} service crash detected"
        body = (
            f"The {service} service heartbeat has been missing for two consecutive checks.\n"
            "The service may have crashed. Please investigate immediately."
        )
        try:
            send_discord_fn(
                webhook_url,
                f"🚨 **{service} service crash detected** — heartbeat missing for 2 checks.",
            )
        except Exception as exc:
            log.error("Failed to send Discord crash alert for %s: %s", service, exc)
        try:
            send_email_fn(sg_api_key, email_from, email_to, subject, body)
        except Exception as exc:
            log.error("Failed to send email crash alert for %s: %s", service, exc)
        mark_crash_alerted(service)
    else:
        # First miss — mark as suspected
        log.warning("%s heartbeat missing — marking as suspected crash", service)
        mark_crash_suspected(service)
