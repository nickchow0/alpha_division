import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger("watchdog.deduplicator")

_EMPTY_STATE = {"error_cooldowns": {}, "restart_counts": {}}


def fingerprint(service: str, error_text: str) -> str:
    raw = f"{service}:{error_text[:300]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load(state_file: str) -> dict:
    if not os.path.exists(state_file):
        return dict(_EMPTY_STATE)
    try:
        with open(state_file) as f:
            return json.load(f)
    except Exception:
        return dict(_EMPTY_STATE)


def _save(state: dict, state_file: str) -> None:
    try:
        with open(state_file, "w") as f:
            json.dump(state, f)
    except Exception as exc:
        log.error(f"Failed to save watchdog state: {exc}")


def is_suppressed(fp: str, state_file: str, cooldown_minutes: int = 10) -> bool:
    state = _load(state_file)
    ts = state["error_cooldowns"].get(fp)
    if not ts:
        return False
    last = datetime.fromisoformat(ts)
    return datetime.now(timezone.utc) - last < timedelta(minutes=cooldown_minutes)


def record_seen(fp: str, state_file: str) -> None:
    state = _load(state_file)
    state["error_cooldowns"][fp] = datetime.now(timezone.utc).isoformat()
    _save(state, state_file)


def record_restart(service: str, state_file: str) -> None:
    state = _load(state_file)
    counts = state.setdefault("restart_counts", {})
    counts.setdefault(service, []).append(datetime.now(timezone.utc).isoformat())
    _save(state, state_file)


def restart_count_in_window(service: str, window_minutes: int, state_file: str) -> int:
    state = _load(state_file)
    timestamps = state.get("restart_counts", {}).get(service, [])
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    return sum(1 for ts in timestamps if datetime.fromisoformat(ts) > cutoff)
