import json
import logging
import subprocess

log = logging.getLogger("watchdog.log_monitor")


def collect_errors(compose_file: str, since_seconds: int = 35) -> list[dict]:
    """
    Return ERROR-level log lines from all Docker containers in the last
    `since_seconds` seconds. Each entry: {"service": str, "line": str, "message": str}
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file, "logs",
             f"--since={since_seconds}s", "--no-color"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning(f"docker compose logs exited {result.returncode}")
            return []
    except Exception as exc:
        log.error(f"Failed to collect logs: {exc}")
        return []

    errors = []
    for raw_line in result.stdout.splitlines():
        if " | " not in raw_line:
            continue
        prefix, _, log_line = raw_line.partition(" | ")
        service = prefix.strip().rsplit("-", 1)[0]

        # Try JSON parse first
        try:
            parsed = json.loads(log_line)
            if parsed.get("level", "").upper() == "ERROR":
                errors.append({
                    "service": service,
                    "line": raw_line,
                    "message": parsed.get("message", log_line),
                })
        except json.JSONDecodeError:
            # Plain text: look for ERROR keyword
            if "ERROR" in log_line or "Error" in log_line:
                errors.append({
                    "service": service,
                    "line": raw_line,
                    "message": log_line.strip(),
                })

    return errors
