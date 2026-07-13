# Log Viewer Dashboard Design

**Date:** 2026-07-13
**Status:** Approved

## Summary

A new `/logs` page in the existing AlphaDivision dashboard (port 8080) that shows filterable log history from all Docker services. The Python `docker` SDK reads logs directly from the Docker socket — no new services required.

---

## Requirements

- New `/logs` page accessible via the dashboard nav bar
- Filterable by: time range, service, log level, keyword
- Time ranges: 15m, 30m, 1h, 3h, 6h, 24h (default 30m)
- Services: analysis, data, execution, dashboard, ml, alerts, research (all selected by default)
- Levels: ALL, INFO, WARNING, ERROR
- Cap at 2000 most-recent lines across all services; show truncation warning if more exist
- No live streaming — re-fetches from server on Apply
- Instant client-side re-filter for level and service toggles after initial load
- Keyword search client-side after load

---

## Architecture

### Data flow

```
User loads /logs
  → JS calls GET /api/logs?since=30m&services=all&level=all&q=
  → log_reader.py uses Python docker SDK to call container.logs() for each service
  → Parses JSON lines (falls back to plain text for non-JSON)
  → Merges, sorts newest-first, caps at limit
  → Returns {"logs": [...], "total_fetched": N, "showing": M, "truncated": bool}
  → JS renders table
  → Level/service toggles re-filter client-side instantly (no re-fetch)
  → Time range change or Apply button re-fetches from server
```

### Files

| File | Change | Role |
|---|---|---|
| `docker-compose.yml` | Modify | Add `/var/run/docker.sock:/var/run/docker.sock` to dashboard volumes |
| `services/dashboard/requirements.txt` | Modify | Add `docker>=7.0.0` |
| `services/dashboard/log_reader.py` | Create | Fetch, parse, filter, sort logs via Docker SDK |
| `services/dashboard/tests/test_log_reader.py` | Create | Tests with mocked Docker SDK |
| `services/dashboard/main.py` | Modify | Add `GET /logs` and `GET /api/logs` routes |
| `services/dashboard/templates/logs.html` | Create | Log viewer page |

---

## Component Design

### `log_reader.py`

```python
SERVICES = ["analysis", "data", "execution", "dashboard", "ml", "alerts", "research"]
CONTAINER_NAME = "alphadivision-{service}-1"
SINCE_MAP = {"15m": 900, "30m": 1800, "1h": 3600, "3h": 10800, "6h": 21600, "24h": 86400}

def fetch_logs(
    since: str = "30m",
    services: list[str] | None = None,
    level: str = "all",
    q: str = "",
    limit: int = 2000,
) -> dict:
    """
    Returns:
      {
        "logs": [{"timestamp": str, "service": str, "level": str, "message": str}, ...],
        "total_fetched": int,
        "showing": int,
        "truncated": bool,
      }
    Sorted newest-first. Capped at limit.
    """
```

**Implementation:**
- Use `docker.DockerClient(base_url="unix://var/run/docker.sock")`
- For each requested service: `client.containers.get(CONTAINER_NAME.format(service=service))`
- Call `container.logs(since=<unix_timestamp>, timestamps=False, stream=False)`
- Parse each line: try `json.loads()` (services emit structured JSON with `timestamp`, `level`, `message`), fall back to `{"timestamp": datetime.now().isoformat(), "service": ..., "level": "INFO", "message": raw_line}`
- Note: `timestamps=False` is used because the services already embed ISO timestamps inside the JSON body. The Docker-prepended timestamp format (`2026-07-13T17:00:00.000000000Z <json_body>`) is not used.
- Known limitation: the `exception` field emitted by some services for tracebacks is not included in the parsed `message` — only the top-level `message` field is shown in the log table.
- Collect all lines, sort by timestamp descending, cap at limit
- If container not found: log warning, skip (don't crash)
- If Docker socket unavailable: return error dict `{"error": "Docker socket unavailable"}`

### `/api/logs` endpoint

```
GET /api/logs
  ?since=30m          (15m | 30m | 1h | 3h | 6h | 24h)
  &services=all       (comma-separated service names or "all")
  &level=all          (all | INFO | WARNING | ERROR)
  &q=                 (keyword substring match on message)
  &limit=2000         (max lines, hard cap 5000)
```

Response:
```json
{
  "logs": [
    {"timestamp": "2026-07-13T17:00:00Z", "service": "analysis", "level": "ERROR", "message": "AI call failed..."},
    ...
  ],
  "total_fetched": 4821,
  "showing": 2000,
  "truncated": true
}
```

### `logs.html`

**Layout:**
- Standard dashboard nav + title "Logs"
- **Filter bar** (sticky top):
  - Time range dropdown: 15m / 30m / 1h / 3h / 6h / 24h
  - Service toggle buttons (pill style): All | analysis | data | execution | dashboard | ml | alerts | research
  - Level pills: ALL | INFO | WARNING | ERROR
  - Keyword search input
  - Apply button (re-fetches server) — also triggers on Enter in search box
- **Status bar**: "Showing 2000 of 4821 lines — oldest omitted" (warning colour if truncated)
- **Log table**:
  - Columns: Timestamp | Service | Level | Message
  - Timestamp: `HH:MM:SS` (local time, full ISO on hover tooltip)
  - Service: coloured badge per service (consistent colour across page loads)
  - Level: coloured text — ERROR red, WARNING amber, INFO grey
  - Message: monospace, full width, wraps
- **Loading spinner** while fetching
- **Empty state**: "No logs match the current filters"

**Client-side behaviour:**
- Service and level toggles filter the already-loaded rows (no re-fetch)
- Keyword search filters client-side as you type (debounced 300ms)
- Time range change triggers re-fetch (clears table, shows spinner)
- Apply button always re-fetches

---

## docker-compose.yml change

Add to `dashboard` service volumes:
```yaml
- /var/run/docker.sock:/var/run/docker.sock:ro
```

Read-only mount: the dashboard only reads logs, never manages containers.

---

## Failure Modes

| Scenario | Behaviour |
|---|---|
| Docker socket not mounted | API returns `{"error": "Docker socket unavailable"}`, page shows error banner |
| Container not found (service down) | Skip that service, continue with others, no crash |
| Container returns non-JSON log lines | Fall back to plain-text entry with level=INFO |
| 24h timerange returns >2000 lines | Return 2000 newest, set `truncated: true` |
| All filters produce empty result | Show "No logs match" empty state |

---

## Out of Scope

- Live streaming / auto-refresh
- Persisting logs to database
- Log download / export
- Watchdog host-service logs (only Docker container logs)
- Log rotation or retention management
