# Log Viewer Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/logs` page to the existing AlphaDivision dashboard that shows filterable log history from all Docker services.

**Architecture:** Python `docker` SDK reads container logs via a mounted Docker socket. A new `log_reader.py` module handles fetching and parsing. Two new routes in `main.py` serve the page and a JSON API. Client-side JS handles level/service toggles and keyword filtering after initial load; time range changes re-fetch from server.

**Tech Stack:** Python 3, `docker` SDK, Flask, vanilla JS, Jinja2

## Global Constraints

- Dashboard runs in Docker on port 8080; host is Oracle Cloud ARM64 Ubuntu VM
- Docker socket mounted read-only: `/var/run/docker.sock:/var/run/docker.sock:ro`
- Container names follow pattern: `alphadivision-{service}-1`
- All services emit structured JSON logs: `{"timestamp": str, "service": str, "level": str, "message": str}`
- Test runner (run from `services/dashboard/`): `python3 -m pytest tests/ -v`
- All external calls mocked in tests — no real Docker socket calls
- `sudo git` for commits; `sudo` not needed for test runs or file reads
- New page matches existing dashboard style — extends `base.html`, uses `data-table` class, follows `trades.html` pattern
- `shared/templates/shared_base.html` is the actual nav template (not `base.html` directly)
- `_inject_nav()` in `main.py` controls `active_page`; add `"logs": "logs"` to `_page_map`
- Max 2000 lines returned by default; hard cap 5000 via `limit` param
- `fetch_logs` and `SERVICES` imported at top level in `main.py` so tests can patch `main.fetch_logs`

---

### Task 1: `log_reader.py` — fetch and parse Docker logs

**Files:**
- Create: `services/dashboard/log_reader.py`
- Create: `services/dashboard/tests/test_log_reader.py`
- Modify: `services/dashboard/requirements.txt`

**Interfaces:**
- Produces:
  - `SERVICES: list[str]` — `["analysis", "data", "execution", "dashboard", "ml", "alerts", "research"]`
  - `fetch_logs(since, services, level, q, limit) -> dict` — `{"logs": [...], "total_fetched": int, "showing": int, "truncated": bool}` or `{"error": str}`
  - `_parse_line(line: str, service: str) -> dict` — `{"timestamp": str, "service": str, "level": str, "message": str}`
- Consumed by: Task 2 (`main.py`)

- [ ] **Step 1: Install docker SDK on host for testing**

```bash
pip3 install docker
```

Expected: `Successfully installed docker-7.x.x` (or already satisfied)

- [ ] **Step 2: Add docker SDK to requirements.txt**

Append to `services/dashboard/requirements.txt`:

```
docker>=7.0.0
```

- [ ] **Step 3: Write the failing tests**

Create `services/dashboard/tests/test_log_reader.py`:

```python
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock

import docker as docker_module
from log_reader import fetch_logs, _parse_line, SERVICES


def _make_log(service, level, message, ts="2026-07-13T17:00:00+00:00"):
    return json.dumps({
        "timestamp": ts, "service": service,
        "level": level, "message": message,
    })


def _mock_client(container_logs: dict):
    """container_logs: {service_name: [json_line_str, ...]}"""
    mock_client = MagicMock()

    def get_container(name):
        service = name.replace("alphadivision-", "").replace("-1", "")
        if service not in container_logs:
            raise docker_module.errors.NotFound("not found")
        mock_container = MagicMock()
        lines = container_logs[service]
        mock_container.logs.return_value = ("\n".join(lines) + "\n").encode()
        return mock_container

    mock_client.containers.get.side_effect = get_container
    return mock_client


def test_fetch_logs_returns_entries():
    logs = {"analysis": [_make_log("analysis", "INFO", "Stage 1 passed")]}
    with patch("log_reader.docker.DockerClient", return_value=_mock_client(logs)):
        result = fetch_logs(since="30m", services=["analysis"])
    assert result["showing"] == 1
    assert result["logs"][0]["message"] == "Stage 1 passed"
    assert result["logs"][0]["level"] == "INFO"


def test_fetch_logs_filters_by_level():
    logs = {
        "analysis": [
            _make_log("analysis", "INFO", "ok"),
            _make_log("analysis", "ERROR", "AI call failed"),
        ]
    }
    with patch("log_reader.docker.DockerClient", return_value=_mock_client(logs)):
        result = fetch_logs(services=["analysis"], level="ERROR")
    assert result["showing"] == 1
    assert result["logs"][0]["level"] == "ERROR"


def test_fetch_logs_filters_by_keyword():
    logs = {
        "analysis": [
            _make_log("analysis", "ERROR", "AI call failed: Connection refused"),
            _make_log("analysis", "ERROR", "Stage 1 filter failed"),
        ]
    }
    with patch("log_reader.docker.DockerClient", return_value=_mock_client(logs)):
        result = fetch_logs(services=["analysis"], q="Connection")
    assert result["showing"] == 1
    assert "Connection" in result["logs"][0]["message"]


def test_fetch_logs_sorted_newest_first():
    logs = {
        "analysis": [
            _make_log("analysis", "INFO", "first",  ts="2026-07-13T17:00:00+00:00"),
            _make_log("analysis", "INFO", "second", ts="2026-07-13T17:00:01+00:00"),
        ]
    }
    with patch("log_reader.docker.DockerClient", return_value=_mock_client(logs)):
        result = fetch_logs(services=["analysis"])
    assert result["logs"][0]["message"] == "second"
    assert result["logs"][1]["message"] == "first"


def test_fetch_logs_truncates_at_limit():
    entries = [_make_log("analysis", "INFO", f"msg {i}") for i in range(10)]
    with patch("log_reader.docker.DockerClient", return_value=_mock_client({"analysis": entries})):
        result = fetch_logs(services=["analysis"], limit=5)
    assert result["showing"] == 5
    assert result["total_fetched"] == 10
    assert result["truncated"] is True


def test_fetch_logs_missing_container_skips():
    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker_module.errors.NotFound("nope")
    with patch("log_reader.docker.DockerClient", return_value=mock_client):
        result = fetch_logs(services=["analysis"])
    assert result["showing"] == 0
    assert "error" not in result


def test_fetch_logs_docker_unavailable_returns_error():
    with patch("log_reader.docker.DockerClient", side_effect=Exception("socket not found")):
        result = fetch_logs()
    assert "error" in result


def test_parse_line_json():
    line = '{"timestamp": "2026-07-13T17:00:00+00:00", "service": "analysis", "level": "ERROR", "message": "oops"}'
    entry = _parse_line(line, "analysis")
    assert entry["level"] == "ERROR"
    assert entry["message"] == "oops"
    assert entry["service"] == "analysis"


def test_parse_line_non_json_falls_back():
    entry = _parse_line("plain text log line", "data")
    assert entry["service"] == "data"
    assert entry["message"] == "plain text log line"
    assert entry["level"] == "INFO"
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd /opt/alphadivision/services/dashboard && python3 -m pytest tests/test_log_reader.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'log_reader'`

- [ ] **Step 5: Implement `log_reader.py`**

Create `services/dashboard/log_reader.py`:

```python
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
    for service in services:
        name = _CONTAINER.format(service=service)
        try:
            container = client.containers.get(name)
            raw = container.logs(since=since_ts, timestamps=False, stream=False)
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
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /opt/alphadivision/services/dashboard && python3 -m pytest tests/test_log_reader.py -v
```

Expected: 9 passed

- [ ] **Step 7: Commit**

```bash
sudo git -C /opt/alphadivision add services/dashboard/log_reader.py services/dashboard/tests/test_log_reader.py services/dashboard/requirements.txt
sudo git -C /opt/alphadivision commit -m "feat(dashboard): add log_reader module with Docker SDK log fetching"
```

---

### Task 2: `/logs` page route and `/api/logs` API route

**Files:**
- Modify: `services/dashboard/main.py`
- Modify: `services/dashboard/tests/test_main.py`
- Modify: `shared/templates/shared_base.html`

**Interfaces:**
- Consumes: `fetch_logs`, `SERVICES` from `log_reader` (already on disk from Task 1)
- Produces:
  - `GET /logs` → renders `logs.html` (created in Task 3)
  - `GET /api/logs?since&services&level&q&limit` → JSON response

- [ ] **Step 1: Write the failing tests**

Append to `services/dashboard/tests/test_main.py` (after the last class):

```python
class TestLogsRoutes(unittest.TestCase):
    def setUp(self):
        import main
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()

    @patch("main.fetch_logs", return_value={
        "logs": [
            {"timestamp": "2026-07-13T17:00:00+00:00", "service": "analysis",
             "level": "ERROR", "message": "AI call failed"}
        ],
        "total_fetched": 1, "showing": 1, "truncated": False,
    })
    def test_api_logs_returns_json(self, mock_fetch):
        resp = self.client.get("/api/logs?since=30m&level=ERROR&services=analysis")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("logs", data)
        self.assertEqual(len(data["logs"]), 1)
        mock_fetch.assert_called_once_with(
            since="30m", services=["analysis"], level="ERROR", q="", limit=2000,
        )

    @patch("main.fetch_logs", return_value={
        "logs": [], "total_fetched": 0, "showing": 0, "truncated": False,
    })
    def test_api_logs_all_services_passes_full_list(self, mock_fetch):
        from log_reader import SERVICES
        self.client.get("/api/logs?services=all")
        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args.kwargs["services"], SERVICES)

    @patch("main.fetch_logs", return_value={
        "logs": [], "total_fetched": 0, "showing": 0, "truncated": False,
    })
    def test_api_logs_respects_limit(self, mock_fetch):
        self.client.get("/api/logs?limit=500")
        self.assertEqual(mock_fetch.call_args.kwargs["limit"], 500)

    @patch("main.fetch_logs", return_value={
        "logs": [], "total_fetched": 0, "showing": 0, "truncated": False,
    })
    def test_api_logs_caps_limit_at_5000(self, mock_fetch):
        self.client.get("/api/logs?limit=99999")
        self.assertEqual(mock_fetch.call_args.kwargs["limit"], 5000)

    def test_logs_page_renders(self):
        resp = self.client.get("/logs")
        self.assertEqual(resp.status_code, 200)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /opt/alphadivision/services/dashboard && python3 -m pytest tests/test_main.py::TestLogsRoutes -v
```

Expected: FAIL with 404 errors (routes not yet defined)

- [ ] **Step 3: Add import to top of `main.py`**

After the existing imports in `services/dashboard/main.py`, add:

```python
from log_reader import fetch_logs, SERVICES as _LOG_SERVICES
```

- [ ] **Step 4: Add `"logs"` to `_page_map` in `_inject_nav()`**

In the `_page_map` dict inside `_inject_nav()` in `main.py`, add:

```python
        "logs":      "logs",
```

- [ ] **Step 5: Add routes to `main.py`**

After the `proxy_ml_status` route (near the bottom of `main.py`, before `if __name__ == "__main__":`), add:

```python
@app.route("/logs")
def logs():
    return render_template("logs.html")


@app.route("/api/logs")
def api_logs():
    since = request.args.get("since", "30m")
    services_param = request.args.get("services", "all")
    level = request.args.get("level", "all")
    q = request.args.get("q", "")
    try:
        limit = min(int(request.args.get("limit", 2000)), 5000)
    except ValueError:
        limit = 2000

    services = _LOG_SERVICES if services_param == "all" else [
        s.strip() for s in services_param.split(",") if s.strip()
    ]
    result = fetch_logs(since=since, services=services, level=level, q=q, limit=limit)
    return jsonify(result)
```

- [ ] **Step 6: Add "Logs" link to both navs in `shared/templates/shared_base.html`**

In the `<header class="navbar">` `<nav class="nav-links">` block, after the Settings link, add:

```html
      <a href="/logs"     class="{{ 'active' if active_page == 'logs'     else '' }}">Logs</a>
```

In the `<nav class="tab-bar">` block, after the Charts link, add:

```html
    <a href="/logs"    class="{{ 'active' if active_page == 'logs'    else '' }}"><span>Logs</span></a>
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd /opt/alphadivision/services/dashboard && python3 -m pytest tests/test_main.py -v
```

Expected: all pass (including TestLogsRoutes — 5 new tests)

- [ ] **Step 8: Commit**

```bash
sudo git -C /opt/alphadivision add services/dashboard/main.py services/dashboard/tests/test_main.py shared/templates/shared_base.html
sudo git -C /opt/alphadivision commit -m "feat(dashboard): add /logs page and /api/logs endpoint"
```

---

### Task 3: `logs.html` + docker-compose.yml + deploy

**Files:**
- Create: `services/dashboard/templates/logs.html`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: `GET /api/logs` from Task 2
- Produces: working `/logs` page at `http://<host>:8080/logs` with filter bar, log table, and client-side filtering

- [ ] **Step 1: Create `services/dashboard/templates/logs.html`**

Create `services/dashboard/templates/logs.html`:

```html
{% extends "base.html" %}
{% block title %} — Logs{% endblock %}

{% block content %}
<h1>Logs</h1>

<div id="log-filters" style="display:flex;flex-wrap:wrap;gap:0.5rem;align-items:center;margin-bottom:1rem;">
  <select id="since-select">
    <option value="15m">Last 15 min</option>
    <option value="30m" selected>Last 30 min</option>
    <option value="1h">Last 1 hour</option>
    <option value="3h">Last 3 hours</option>
    <option value="6h">Last 6 hours</option>
    <option value="24h">Last 24 hours</option>
  </select>

  <div id="service-filters" style="display:flex;flex-wrap:wrap;gap:0.25rem;">
    <button class="filter-btn svc-btn active" data-service="all">All</button>
    <button class="filter-btn svc-btn" data-service="analysis">analysis</button>
    <button class="filter-btn svc-btn" data-service="data">data</button>
    <button class="filter-btn svc-btn" data-service="execution">execution</button>
    <button class="filter-btn svc-btn" data-service="dashboard">dashboard</button>
    <button class="filter-btn svc-btn" data-service="ml">ml</button>
    <button class="filter-btn svc-btn" data-service="alerts">alerts</button>
    <button class="filter-btn svc-btn" data-service="research">research</button>
  </div>

  <div id="level-filters" style="display:flex;gap:0.25rem;">
    <button class="filter-btn lvl-btn active" data-level="all">ALL</button>
    <button class="filter-btn lvl-btn" data-level="INFO">INFO</button>
    <button class="filter-btn lvl-btn" data-level="WARNING">WARN</button>
    <button class="filter-btn lvl-btn" data-level="ERROR">ERROR</button>
  </div>

  <input id="kw-input" type="text" placeholder="Search..."
    style="flex:1;min-width:120px;max-width:260px;padding:3px 8px;border:1px solid #444;border-radius:4px;background:transparent;">
  <button id="apply-btn" class="btn-primary">Apply</button>
</div>

<div id="log-status" style="margin-bottom:0.5rem;font-size:0.85rem;color:var(--muted,#888);min-height:1.2em;"></div>
<div id="log-spinner" style="display:none;margin-bottom:0.5rem;">Loading…</div>

<div style="overflow-x:auto;">
  <table class="data-table" id="log-table">
    <thead>
      <tr>
        <th style="white-space:nowrap;">Time</th>
        <th>Service</th>
        <th>Level</th>
        <th style="width:100%;">Message</th>
      </tr>
    </thead>
    <tbody id="log-body"></tbody>
  </table>
</div>
<p id="log-empty" style="display:none;" class="empty-state">No logs match the current filters.</p>
{% endblock %}

{% block extra_js %}
<script>
(function () {
  const SERVICE_COLORS = {
    analysis:'#3b82f6', data:'#10b981', execution:'#ef4444',
    dashboard:'#8b5cf6', ml:'#f59e0b', alerts:'#ec4899', research:'#06b6d4',
  };
  const LEVEL_COLORS = { ERROR:'#ef4444', WARNING:'#f59e0b' };

  let allLogs = [];
  let activeServices = new Set(['all']);
  let activeLevel = 'all';
  let kwFilter = '';

  function loadLogs() {
    const since = document.getElementById('since-select').value;
    const svcParam = activeServices.has('all') ? 'all' : [...activeServices].join(',');
    const url = `/api/logs?since=${since}&services=${encodeURIComponent(svcParam)}`;

    document.getElementById('log-spinner').style.display = '';
    document.getElementById('log-status').textContent = '';
    document.getElementById('log-body').innerHTML = '';
    document.getElementById('log-empty').style.display = 'none';

    fetch(url)
      .then(r => r.json())
      .then(data => {
        document.getElementById('log-spinner').style.display = 'none';
        if (data.error) {
          document.getElementById('log-status').textContent = '⚠ ' + data.error;
          return;
        }
        allLogs = data.logs || [];
        const status = data.truncated
          ? `⚠ Showing ${data.showing.toLocaleString()} of ${data.total_fetched.toLocaleString()} lines — oldest omitted`
          : `Showing ${data.showing.toLocaleString()} lines`;
        document.getElementById('log-status').textContent = status;
        renderTable();
      })
      .catch(err => {
        document.getElementById('log-spinner').style.display = 'none';
        document.getElementById('log-status').textContent = '⚠ Fetch failed: ' + err;
      });
  }

  function renderTable() {
    const tbody = document.getElementById('log-body');
    const kw = kwFilter.toLowerCase();
    const visible = allLogs.filter(e => {
      if (activeLevel !== 'all' && e.level.toUpperCase() !== activeLevel) return false;
      if (kw && !e.message.toLowerCase().includes(kw)) return false;
      return true;
    });
    document.getElementById('log-empty').style.display = visible.length ? 'none' : '';
    tbody.innerHTML = visible.map(e => {
      const ts = (e.timestamp || '').replace('T', ' ').substring(0, 19);
      const col = SERVICE_COLORS[e.service] || '#6b7280';
      const lvlCol = LEVEL_COLORS[e.level.toUpperCase()] || '';
      const badge = `<span style="background:${col};color:#fff;padding:1px 6px;border-radius:3px;font-size:0.78rem;">${esc(e.service)}</span>`;
      const lvl = lvlCol
        ? `<span style="color:${lvlCol};font-weight:600;">${esc(e.level)}</span>`
        : `<span>${esc(e.level)}</span>`;
      return `<tr>
        <td class="ts" style="white-space:nowrap;font-family:monospace;font-size:0.82rem;">${esc(ts)}</td>
        <td>${badge}</td><td>${lvl}</td>
        <td style="font-family:monospace;font-size:0.82rem;word-break:break-all;">${esc(e.message)}</td>
      </tr>`;
    }).join('');
  }

  function esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  document.querySelectorAll('.svc-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const svc = btn.dataset.service;
      if (svc === 'all') { activeServices = new Set(['all']); }
      else {
        activeServices.delete('all');
        activeServices.has(svc) ? activeServices.delete(svc) : activeServices.add(svc);
        if (!activeServices.size) activeServices = new Set(['all']);
      }
      document.querySelectorAll('.svc-btn').forEach(b =>
        b.classList.toggle('active', activeServices.has(b.dataset.service)));
      loadLogs();
    });
  });

  document.querySelectorAll('.lvl-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      activeLevel = btn.dataset.level;
      document.querySelectorAll('.lvl-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.level === activeLevel));
      renderTable();
    });
  });

  let kwTimer;
  document.getElementById('kw-input').addEventListener('input', e => {
    clearTimeout(kwTimer);
    kwTimer = setTimeout(() => { kwFilter = e.target.value; renderTable(); }, 300);
  });

  document.getElementById('apply-btn').addEventListener('click', loadLogs);
  document.getElementById('since-select').addEventListener('change', loadLogs);

  const style = document.createElement('style');
  style.textContent = `
    .filter-btn{padding:3px 10px;border:1px solid #555;border-radius:4px;background:transparent;cursor:pointer;font-size:0.82rem;}
    .filter-btn.active{background:#3b82f6;color:#fff;border-color:#3b82f6;}
    .btn-primary{padding:4px 14px;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer;}
  `;
  document.head.appendChild(style);

  loadLogs();
})();
</script>
{% endblock %}
```

- [ ] **Step 2: Add Docker socket mount to `docker-compose.yml`**

In the `dashboard` service `volumes:` list, add:

```yaml
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

- [ ] **Step 3: Rebuild and restart the dashboard container**

```bash
sudo docker compose build dashboard && sudo docker compose up -d dashboard
```

Expected: build completes (picks up `docker>=7.0.0` from requirements.txt), container restarts

- [ ] **Step 4: Verify the page and API work**

```bash
sleep 5
curl -s http://localhost:8080/logs | grep -c "Logs"
curl -s "http://localhost:8080/api/logs?since=5m&services=analysis" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'showing={d[\"showing\"]} truncated={d[\"truncated\"]}')"
```

Expected:
- First command: `1` (page renders with "Logs" in it)
- Second command: `showing=<N> truncated=False` (N ≥ 0)

- [ ] **Step 5: Run full dashboard test suite**

```bash
cd /opt/alphadivision/services/dashboard && python3 -m pytest tests/ -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
sudo git -C /opt/alphadivision add services/dashboard/templates/logs.html docker-compose.yml
sudo git -C /opt/alphadivision commit -m "feat(dashboard): add logs.html page and mount Docker socket for log reading"
```
