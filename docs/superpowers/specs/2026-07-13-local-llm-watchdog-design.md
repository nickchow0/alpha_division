# Local LLM Watchdog Agent Design

**Date:** 2026-07-13
**Status:** Approved

## Summary

A host-based Python service that monitors all AlphaDivision Docker container logs for errors, uses a local Ollama model (deepseek-r1:7b) to classify each error and decide on a fix, executes the fix autonomously, verifies it worked, and reports everything to Discord. The execution service is never touched automatically — issues there always require human intervention.

---

## Requirements

- Poll all Docker container logs every 30 seconds for `ERROR`-level lines
- Deduplicate: same error fingerprint suppressed for 10 minutes after first occurrence
- Send each novel error to deepseek-r1:7b via Ollama to classify and choose an action
- Execute the chosen action autonomously and report to Discord
- Verify the fix: re-check logs 30 seconds after action; escalate to Discord if error persists
- Hard safety rules enforced in code (not by the LLM):
  - `execution` service → always `alert_only`, never restarted
  - Same service: max 3 restarts within 30 minutes before human escalation
  - LLM confidence < 0.7 → `alert_only` regardless of chosen action
- Runs as a systemd service on the host (`ubuntu` user), starts on boot, restarts on crash
- Notifies Discord on every action taken, every escalation, and on watchdog startup/crash
- No Docker container: runs directly on the host to retain access to `systemctl` and the filesystem

---

## Architecture

### Files

| File | Responsibility |
|---|---|
| `services/watchdog/main.py` | Entry point; main 30-second poll loop |
| `services/watchdog/log_monitor.py` | Collects ERROR lines from all containers via `docker compose logs --since` |
| `services/watchdog/error_classifier.py` | Builds Ollama prompt, calls deepseek-r1:7b, parses JSON response |
| `services/watchdog/action_runner.py` | Executes actions; enforces safety rules; runs verify step |
| `services/watchdog/deduplicator.py` | Fingerprints errors; tracks cooldowns in a JSON state file |
| `services/watchdog/notifier.py` | Posts messages to Discord webhook |
| `services/watchdog/requirements.txt` | `requests` only |
| `services/watchdog/watchdog.service` | systemd unit file |

### Data flow

```
poll loop (30s)
  └─ log_monitor: docker compose logs --since 30s
       └─ filter ERROR lines by service
            └─ deduplicator: skip if seen within 10 min
                 └─ error_classifier: POST to deepseek-r1 → JSON action
                      └─ action_runner:
                           ├─ safety checks (execution? confidence < 0.7? restart limit?)
                           ├─ execute action
                           ├─ wait 30s → verify (re-check logs)
                           └─ notifier: Discord report
```

---

## Component Design

### `log_monitor.py`

```python
def collect_errors(compose_file: str, since_seconds: int = 35) -> list[dict]:
    """
    Returns list of {"service": str, "line": str} for all ERROR-level
    log lines from all containers in the last `since_seconds` seconds.
    Runs: docker compose -f <compose_file> logs --since <N>s --no-log-prefix
    Parses service name from the log prefix before filtering for ERROR.
    """
```

### `error_classifier.py`

System prompt describes all services, their roles, and the fixed action vocabulary. User message contains service name + error text (truncated to 800 chars).

**Response schema (JSON):**
```json
{
  "action": "restart_service | rebuild_service | restart_ollama | alert_only | no_action",
  "target": "service_name | null",
  "reasoning": "one sentence",
  "confidence": 0.0–1.0
}
```

**Action vocabulary given to the model:**

| Action | When to use |
|---|---|
| `restart_service` | Service crashed, stale config, connection errors that a restart clears |
| `rebuild_service` | Service running old code after a git push |
| `restart_ollama` | Ollama connection refused, Ollama service crash |
| `alert_only` | Code bug (needs a human fix), unknown error, execution service error |
| `no_action` | Transient/noisy error (e.g. invalid HTTP request, rate-limit warning) |

**Ollama call:**
- Model: `deepseek-r1:7b`
- URL: `http://localhost:11434/v1/chat/completions` (host localhost, not Docker gateway)
- `response_format: {"type": "json_object"}`
- Timeout: 120 seconds (CPU inference)
- On parse failure or timeout: fall back to `alert_only`

### `action_runner.py`

**Safety checks (in order, before any execution):**
1. If `target == "execution"` → override to `alert_only`
2. If `confidence < 0.7` → override to `alert_only`
3. If same service restarted ≥ 3 times in last 30 minutes → override to `alert_only` with escalation message

**Action implementations:**
```python
def restart_service(name: str) -> bool:
    # subprocess: docker compose -f COMPOSE_FILE restart <name>
    # returns True on exit code 0

def rebuild_service(name: str) -> bool:
    # subprocess: docker compose -f COMPOSE_FILE build <name>
    # then: docker compose -f COMPOSE_FILE up -d <name>

def restart_ollama() -> bool:
    # subprocess: sudo systemctl restart ollama
    # wait 5s, then curl http://localhost:11434/api/tags to verify

def alert_only(message: str) -> bool:
    # notifier.send(); returns True always
```

**Verify step:**
After `restart_service` or `rebuild_service`, wait 30 seconds then call `collect_errors(since_seconds=35)` and check if the same error fingerprint appears again. If yes: post escalation alert and mark service as "human required" (suppress further auto-actions for 60 minutes).

### `deduplicator.py`

State stored in `/opt/alphadivision/.watchdog_state.json`:
```json
{
  "error_cooldowns": {"<fingerprint>": "<iso-timestamp-of-last-action>"},
  "restart_counts": {"<service>": [<iso-timestamps-of-restarts>]}
}
```

**Fingerprint**: `sha256(service_name + error_text[:300])[:16]` — short enough to be stable, long enough to distinguish different errors in the same service.

**Cooldown**: 10 minutes for errors, 30-minute window for restart counting.

### `notifier.py`

Reads `DISCORD_WEBHOOK_URL` from environment (same `.env` as other services).

**Message formats:**
- Action taken: `[watchdog] ✅ analysis — restarted (Ollama URL mismatch after config change) — verified clean`
- Action failed verify: `[watchdog] ⚠️ analysis — restarted but error persists — human required`
- Alert only: `[watchdog] 🔴 execution — AI call failed: ... — human action required`
- Escalation: `[watchdog] 🚨 analysis — restarted 3× in 30min, still failing — suppressing auto-actions for 60min`
- Startup: `[watchdog] 🟢 Watchdog started (model: deepseek-r1:7b)`

### `watchdog.service`

```ini
[Unit]
Description=AlphaDivision LLM Watchdog
After=network.target ollama.service docker.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/alphadivision/services/watchdog
EnvironmentFile=/opt/alphadivision/.env
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Configuration

All watchdog config in `config.toml` under `[watchdog]`:

```toml
[watchdog]
poll_interval_seconds    = 30
error_cooldown_minutes   = 10
restart_limit            = 3
restart_window_minutes   = 30
suppression_minutes      = 60
confidence_threshold     = 0.7
ollama_model             = "deepseek-r1:7b"
ollama_base_url          = "http://localhost:11434"
compose_file             = "/opt/alphadivision/docker-compose.yml"
state_file               = "/opt/alphadivision/.watchdog_state.json"
```

Defaults in `shared/config.py` `_DEFAULT_CONFIG`:
```python
"watchdog": {
    "poll_interval_seconds": 30,
    "error_cooldown_minutes": 10,
    "restart_limit": 3,
    "restart_window_minutes": 30,
    "suppression_minutes": 60,
    "confidence_threshold": 0.7,
    "ollama_model": "deepseek-r1:7b",
    "ollama_base_url": "http://localhost:11434",
    "compose_file": "/opt/alphadivision/docker-compose.yml",
    "state_file": "/opt/alphadivision/.watchdog_state.json",
}
```

---

## Failure Modes

| Scenario | Behaviour |
|---|---|
| Ollama is down during classification | Fall back to `alert_only`; log warning |
| deepseek-r1 returns invalid JSON | Fall back to `alert_only` |
| deepseek-r1 confidence < 0.7 | Override to `alert_only` |
| Execution service errors | Always `alert_only` |
| Same service restarted 3× in 30min | `alert_only` + escalation, 60min suppression |
| Verify step finds error still present | Escalation alert, 60min suppression |
| Watchdog itself crashes | systemd restarts it; Discord alert on re-start |
| Docker daemon unreachable | Log error, skip cycle, retry next poll interval |
| Discord webhook fails | Log to journald, continue |

---

## Out of Scope

- Editing `config.toml` automatically (too risky without understanding full context)
- Watching Postgres or Redis (managed by Docker health checks and `restart: always`)
- Alerting on warnings (ERROR level only)
- Multi-turn conversation with the LLM (single-shot classification only)
