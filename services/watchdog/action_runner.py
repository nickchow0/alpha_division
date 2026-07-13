import logging
import subprocess
import time

from deduplicator import fingerprint, record_restart, restart_count_in_window, suppress_extended
from log_monitor import collect_errors
from notifier import send_notification

log = logging.getLogger("watchdog.action_runner")

_PROTECTED_SERVICES = {"execution"}
_RESTARTABLE_SERVICES = {"analysis", "data", "dashboard", "ml", "alerts", "research"}
_VALID_ACTIONS = {"restart_service", "rebuild_service", "restart_ollama", "alert_only", "no_action"}


def run_action(classification: dict, original_error: dict, cfg: dict) -> str:
    """
    Apply safety checks then execute the classified action.
    Returns: "executed", "alert_only", "suppressed", or "no_action"
    """
    w = cfg["watchdog"]
    action = classification.get("action", "alert_only")
    target = classification.get("target")
    reasoning = classification.get("reasoning", "")
    confidence = float(classification.get("confidence", 0.0))
    service = original_error["service"]
    compose_file = w["compose_file"]
    state_file = w["state_file"]
    fp = fingerprint(service, original_error["message"])

    # Safety: execution service is always alert_only
    if target in _PROTECTED_SERVICES or service in _PROTECTED_SERVICES:
        send_notification(
            f"[watchdog] 🔴 **{service}** — {original_error['message'][:200]}\n"
            f"Reason: {reasoning} — human action required"
        )
        return "alert_only"

    # Safety: low confidence → alert_only
    if confidence < w["confidence_threshold"] and action not in ("no_action", "alert_only"):
        send_notification(
            f"[watchdog] 🔴 **{service}** — {original_error['message'][:200]}\n"
            f"Low confidence ({confidence:.2f}): {reasoning} — human action required"
        )
        return "alert_only"

    # Safety: unknown target → alert_only
    if action in ("restart_service", "rebuild_service") and target not in _RESTARTABLE_SERVICES:
        send_notification(
            f"[watchdog] 🔴 **{service}** — LLM returned unknown target '{target}' — human action required"
        )
        return "alert_only"

    if action == "no_action":
        return "no_action"

    if action == "alert_only":
        send_notification(
            f"[watchdog] 🔴 **{service}** — {original_error['message'][:200]}\n"
            f"{reasoning} — human action required"
        )
        return "alert_only"

    # Safety: restart limit
    if action in ("restart_service", "rebuild_service") and target:
        count = restart_count_in_window(target, w["restart_window_minutes"], state_file)
        if count >= w["restart_limit"]:
            send_notification(
                f"[watchdog] 🚨 **{target}** — restarted {count}x in {w['restart_window_minutes']}min, "
                f"still failing — suppressing auto-actions for {w['suppression_minutes']}min"
            )
            suppress_extended(fp, state_file, w["suppression_minutes"])
            return "alert_only"

    # Execute
    success = False
    if action == "restart_service" and target:
        success = _restart_service(target, compose_file)
        if success:
            record_restart(target, state_file)
    elif action == "rebuild_service" and target:
        success = _rebuild_service(target, compose_file)
        if success:
            record_restart(target, state_file)
    elif action == "restart_ollama":
        success = _restart_ollama()

    if success:
        # Verify: wait then re-check logs
        time.sleep(30)
        still_erroring = collect_errors(compose_file, since_seconds=35)
        same_error = any(
            e["service"] == service and e["message"][:100] == original_error["message"][:100]
            for e in still_erroring
        )
        if same_error:
            send_notification(
                f"[watchdog] ⚠️ **{service}** — {action} executed but error persists\n"
                f"{reasoning} — escalating to human"
            )
            suppress_extended(fp, state_file, w["suppression_minutes"])
        else:
            send_notification(
                f"[watchdog] ✅ **{service}** — {action} (target: {target}) succeeded\n"
                f"{reasoning}"
            )
    else:
        send_notification(
            f"[watchdog] ⚠️ **{service}** — {action} failed to execute\n"
            f"{reasoning} — human action required"
        )

    return "executed"


def _restart_service(name: str, compose_file: str) -> bool:
    result = subprocess.run(
        ["docker", "compose", "-f", compose_file, "restart", name],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        log.error(f"restart {name} failed: {result.stderr}")
    return result.returncode == 0


def _rebuild_service(name: str, compose_file: str) -> bool:
    build = subprocess.run(
        ["docker", "compose", "-f", compose_file, "build", name],
        capture_output=True, text=True, timeout=300,
    )
    if build.returncode != 0:
        log.error(f"build {name} failed: {build.stderr}")
        return False
    up = subprocess.run(
        ["docker", "compose", "-f", compose_file, "up", "-d", name],
        capture_output=True, text=True, timeout=60,
    )
    return up.returncode == 0


def _restart_ollama() -> bool:
    result = subprocess.run(
        ["sudo", "systemctl", "restart", "ollama"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        time.sleep(5)
    return result.returncode == 0
