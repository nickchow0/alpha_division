import json
import logging
import sys
import os
import time
from datetime import datetime, timezone

sys.path.insert(0, "/opt/alphadivision")
from shared.config import load_config

import docker
import docker.errors

log = logging.getLogger("dashboard.log_reader")

SERVICES = ["analysis", "data", "execution", "dashboard", "ml", "alerts", "research", "watchdog"]
_CONTAINER = "alphadivision-{service}-1"
_SINCE_SECONDS = {
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "3h": 10800,
    "6h": 21600,
    "24h": 86400,
}


def fetch_logs(
    since: str = "30m",
    services: list = None,
    level: str = "all",
    q: str = "",
    limit: int = 2000,
) -> dict:
    if services is None:
        services = SERVICES

    since_seconds = _SINCE_SECONDS.get(since, 1800)
    since_ts = int(time.time()) - since_seconds

    try:
        client = docker.DockerClient(base_url="unix://var/run/docker.sock")
    except Exception as exc:
        log.error(f"Docker socket unavailable: {exc}")
        return {"error": "Docker socket unavailable"}

    all_entries = []
    try:
        for service in services:
            if service == "watchdog":
                continue
            name = _CONTAINER.format(service=service)
            try:
                container = client.containers.get(name)
                raw = container.logs(since=since_ts, timestamps=False, stream=False, tail=5000)
                text = raw.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    all_entries.append(_parse_line(line, service))
            except docker.errors.NotFound:
                log.warning(f"Container {name} not found — skipping")
            except Exception as exc:
                log.warning(f"Failed to read logs from {name}: {exc}")
    finally:
        client.close()

    # Read watchdog log file (host-side file, not a Docker container)
    if "watchdog" in services:
        try:
            cfg = load_config()
            watchdog_path = cfg["watchdog"]["log_file"]
            all_entries += _fetch_watchdog_logs(watchdog_path, since_seconds)
        except Exception as exc:
            log.warning(f"Failed to load watchdog log path from config: {exc}")

    level_upper = level.upper()
    if level_upper != "ALL":
        all_entries = [e for e in all_entries if e["level"].upper() == level_upper]

    if q:
        q_lower = q.lower()
        all_entries = [e for e in all_entries if q_lower in e["message"].lower()]

    total = len(all_entries)
    all_entries.sort(key=lambda e: e["timestamp"], reverse=True)

    return {
        "logs": all_entries[:limit],
        "total_fetched": total,
        "showing": min(total, limit),
        "truncated": total > limit,
    }


def _parse_line(line: str, service: str) -> dict:
    try:
        parsed = json.loads(line)
        return {
            "timestamp": parsed.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "service": parsed.get("service", service),
            "level": parsed.get("level", "INFO"),
            "message": parsed.get("message", line),
        }
    except json.JSONDecodeError:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": service,
            "level": "INFO",
            "message": line,
        }


def _fetch_watchdog_logs(path: str, since_seconds: int) -> list:
    cutoff = time.time() - since_seconds
    entries = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                entry = _parse_line(raw, "watchdog")
                entry["service"] = "watchdog"
                try:
                    entry_ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                    if entry_ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
                entries.append(entry)
    except FileNotFoundError:
        return []
    except Exception as exc:
        log.warning(f"Failed to read watchdog log {path}: {exc}")
    return entries
