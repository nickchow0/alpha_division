import json
import logging
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/opt/alphadivision")

from shared.config import load_config
from shared.redis_client import get_redis
from deduplicator import fingerprint, is_suppressed, record_seen
from error_classifier import classify_error
from action_runner import run_action
from log_monitor import collect_errors
from notifier import send_notification


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        })


def _add_file_handler(log_file: str) -> None:
    handler = logging.FileHandler(log_file)
    handler.setFormatter(_JsonFormatter())
    logging.getLogger().addHandler(handler)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("watchdog")

_HEARTBEAT_KEY = "heartbeat:watchdog"
_HEARTBEAT_TTL = 90        # seconds — refreshed every 60s so TTL never expires during normal operation
_HEARTBEAT_INTERVAL = 60   # seconds


def _publish_heartbeat() -> None:
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


def run_once(cfg: dict) -> None:
    """Run one poll cycle — collect errors, classify, act."""
    w = cfg["watchdog"]
    errors = collect_errors(w["compose_file"])

    for error in errors:
        fp = fingerprint(error["service"], error["message"])
        if is_suppressed(fp, w["state_file"], cooldown_minutes=w["error_cooldown_minutes"]):
            continue

        # Record before classifying so a crash during classify doesn't re-fire
        record_seen(fp, w["state_file"])

        try:
            classification = classify_error(
                error["service"], error["message"],
                w["ollama_base_url"], w["ollama_model"],
            )
        except Exception as exc:
            log.error(f"classify_error raised unexpectedly: {exc}")
            send_notification(
                f"[watchdog] ⚠️ **{error['service']}** — {error['message'][:200]}\n"
                f"Classifier failed: {exc} — human action required"
            )
            continue

        run_action(classification, error, cfg)


def main() -> None:
    cfg = load_config()
    w = cfg["watchdog"]
    _add_file_handler(w["log_file"])
    log.info(f"Watchdog starting (model: {w['ollama_model']}, interval: {w['poll_interval_seconds']}s)")
    send_notification(f"[watchdog] \U0001f7e2 Watchdog started (model: {w['ollama_model']})")

    last_heartbeat = 0.0
    while True:
        now = time.time()
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            try:
                _publish_heartbeat()
            except Exception as exc:
                log.error("Heartbeat failed: %s", exc)
            last_heartbeat = now
        try:
            run_once(cfg)
            # Reload config each cycle so dashboard changes take effect
            cfg = load_config()
        except Exception as exc:
            log.error(f"Poll cycle failed: {exc}")
        time.sleep(w["poll_interval_seconds"])


if __name__ == "__main__":
    main()
