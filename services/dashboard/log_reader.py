import json
import logging
import time
from datetime import datetime, timezone

import docker
import docker.errors

log = logging.getLogger("dashboard.log_reader")

SERVICES = ["analysis", "data", "execution", "dashboard", "ml", "alerts", "research"]
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
