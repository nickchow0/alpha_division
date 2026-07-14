# Dashboard Service Heartbeats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `research`, `ml`, and `watchdog` to the dashboard's Services heartbeat checklist by extending the existing Redis-heartbeat pattern already used by `data`/`analysis`/`execution`/`alerts` — no new mechanism.

**Architecture:** Each of `ml` and `watchdog` already runs its own `while True: ...` loop — add a timestamp-gated `_publish_heartbeat()` call inside each, identical in shape to the four services that already do this. `research` has no main loop (gunicorn serves the Flask app directly), so it gets a daemon background thread started at module level instead, ticking on its own 60s timer independent of HTTP traffic. Once all three publish `heartbeat:<name>`, a one-line change to `services/dashboard/service_status.py`'s `MONITORED_SERVICES` list makes the dashboard pick them up — its existing test suite already asserts generically against that list, so no dashboard test changes are needed.

**Tech Stack:** Python 3, `redis-py` (via `shared.redis_client.get_redis`), `threading`/`time` stdlib.

## Global Constraints

- Every new heartbeat uses the exact same shape as the existing ones (`analysis`/`execution`/`alerts`): a `_HEARTBEAT_KEY = "heartbeat:<name>"`, `_HEARTBEAT_TTL = 90` (seconds), `_HEARTBEAT_INTERVAL = 60` (seconds), and a `_publish_heartbeat()` function that calls `get_redis().setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")`.
- Heartbeat publish failures are caught and logged (`log.error(...)`), never allowed to crash the surrounding loop/thread — matching how `analysis/main.py`'s `_publish_heartbeat` call site already handles this.
- `research`'s heartbeat thread must be started at **module level** (not inside `if __name__ == "__main__":`), since gunicorn imports `main:app` directly and never executes that guard. This means the thread will also start during test collection (any test file that does `from main import app` triggers it) — this is an accepted, intentional tradeoff: the thread's Redis call is wrapped in try/except, so an unmocked/unreachable Redis during test runs just logs an error from a background daemon thread and does not affect test correctness, exit codes, or process termination.
- Do not modify `services/dashboard/tests/test_service_status.py` — it already asserts generically against `MONITORED_SERVICES` and needs no changes.
- Do not modify the `api_health` mechanism, the `ml_runs` table/display, or any template — out of scope per the design spec.

---

## File Structure

- **Modify:** `services/ml/pipeline.py` — heartbeat constants + `_publish_heartbeat()` + call site in `main()`'s loop.
- **Create:** `services/ml/tests/test_pipeline_heartbeat.py` — unit test for the new function.
- **Modify:** `services/watchdog/main.py` — new `get_redis` import + heartbeat constants + `_publish_heartbeat()` + call site in `main()`'s loop.
- **Create:** `services/watchdog/tests/test_heartbeat.py` — unit test for the new function.
- **Modify:** `services/research/main.py` — new `threading`/`time`/`get_redis` imports + heartbeat constants + `_publish_heartbeat()` + `_start_heartbeat_thread()`, called at module level.
- **Create:** `services/research/tests/test_heartbeat.py` — unit test for the new function (not the thread/loop itself).
- **Modify:** `services/dashboard/service_status.py` — `MONITORED_SERVICES` gains the three new names.

---

### Task 1: Add heartbeat publishing to services/ml/pipeline.py

**Files:**
- Modify: `services/ml/pipeline.py`
- Test: `services/ml/tests/test_pipeline_heartbeat.py`

**Interfaces:**
- Consumes: `get_redis` (already imported at `services/ml/pipeline.py:26`), `log` (already defined at `services/ml/pipeline.py:40` as `logging.getLogger("ml")`).
- Produces: `_publish_heartbeat()`, `_HEARTBEAT_KEY = "heartbeat:ml"`, `_HEARTBEAT_TTL`, `_HEARTBEAT_INTERVAL` — no other task consumes these.

- [ ] **Step 1: Write the failing test**

Create `/opt/alphadivision/services/ml/tests/test_pipeline_heartbeat.py`:

```python
"""Unit tests for pipeline.py's heartbeat publishing — all Redis I/O is mocked."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from unittest.mock import MagicMock, patch


@patch("pipeline.get_redis")
def test_publish_heartbeat_sets_correct_key_and_ttl(mock_get_redis):
    from pipeline import _publish_heartbeat, _HEARTBEAT_KEY, _HEARTBEAT_TTL

    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    _publish_heartbeat()

    mock_redis.setex.assert_called_once_with(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
sudo docker exec -w /app alphadivision-ml-1 pip install --quiet pytest
sudo docker exec -w /app alphadivision-ml-1 python -m pytest tests/test_pipeline_heartbeat.py -v
```
Expected: FAIL — `ImportError: cannot import name '_publish_heartbeat' from 'pipeline'` (or `_HEARTBEAT_KEY` not defined).

- [ ] **Step 3: Implement**

Read `/opt/alphadivision/services/ml/pipeline.py` first. Insert these constants and function right before `def _start_health_server():` (currently at line 324):

```python
_HEARTBEAT_KEY = "heartbeat:ml"
_HEARTBEAT_TTL = 90        # seconds — refreshed every 60s so TTL never expires during normal operation
_HEARTBEAT_INTERVAL = 60   # seconds


def _publish_heartbeat() -> None:
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


```

Then change `main()` (currently):
```python
def main() -> None:
    cfg = load_config().get("ml", {})
    cron = cfg.get("cron_schedule", "0 2 * * *")

    # Ensure ML tables exist before first run
    ensure_ml_tables()

    _start_health_server()

    # Parse cron schedule: "0 2 * * *" → 02:00 UTC
    parts  = cron.split()
    minute = int(parts[0])
    hour   = int(parts[1])
    schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(run_pipeline)
    log.info("Pipeline scheduled at %02d:%02d UTC nightly", hour, minute)

    # Run once immediately on startup
    run_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(30)
```
to:
```python
def main() -> None:
    cfg = load_config().get("ml", {})
    cron = cfg.get("cron_schedule", "0 2 * * *")

    # Ensure ML tables exist before first run
    ensure_ml_tables()

    _start_health_server()

    # Parse cron schedule: "0 2 * * *" → 02:00 UTC
    parts  = cron.split()
    minute = int(parts[0])
    hour   = int(parts[1])
    schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(run_pipeline)
    log.info("Pipeline scheduled at %02d:%02d UTC nightly", hour, minute)

    # Run once immediately on startup
    run_pipeline()

    last_heartbeat = 0.0
    while True:
        schedule.run_pending()
        now = time.time()
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            try:
                _publish_heartbeat()
            except Exception as exc:
                log.error("Heartbeat failed: %s", exc)
            last_heartbeat = now
        time.sleep(30)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
sudo docker exec -w /app alphadivision-ml-1 python -m pytest tests/test_pipeline_heartbeat.py -v
```
Expected: `1 passed`

- [ ] **Step 5: Confirm no existing ml tests broke**

```bash
sudo docker exec -w /app alphadivision-ml-1 python -m pytest tests/ -v
```
Expected: all tests pass (existing count plus the 1 new test).

- [ ] **Step 6: Live-verify (safe — the ml container will restart to pick up the code change; this does not touch `execution` or any trading logic)**

```bash
sudo docker compose -f /opt/alphadivision/docker-compose.yml up -d --force-recreate ml
sleep 5
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:ml
```
Expected: a positive integer (≤ 90, since the container just started and published its first heartbeat within the first 30s loop tick).

- [ ] **Step 7: Commit**

```bash
cd /opt/alphadivision
git add services/ml/pipeline.py services/ml/tests/test_pipeline_heartbeat.py
git commit -m "$(cat <<'EOF'
feat(ml): publish a heartbeat so the dashboard can monitor this service

Extends the existing heartbeat:<service> Redis pattern already used
by analysis/execution/alerts to ml, which already has get_redis and a
main loop — this is the cheapest of the three additions in this plan.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add heartbeat publishing to services/watchdog/main.py

**Files:**
- Modify: `services/watchdog/main.py`
- Test: `services/watchdog/tests/test_heartbeat.py`

**Interfaces:**
- Consumes: `log` (already defined as `logging.getLogger("watchdog")`). Does NOT currently import `shared.redis_client` — this task adds that import.
- Produces: `_publish_heartbeat()`, `_HEARTBEAT_KEY = "heartbeat:watchdog"`, `_HEARTBEAT_TTL`, `_HEARTBEAT_INTERVAL` — no other task consumes these.

- [ ] **Step 1: Write the failing test**

Create `/opt/alphadivision/services/watchdog/tests/test_heartbeat.py`:

```python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import patch, MagicMock


class TestPublishHeartbeat(unittest.TestCase):
    @patch("main.get_redis")
    def test_publish_heartbeat_sets_correct_key_and_ttl(self, mock_get_redis):
        from main import _publish_heartbeat, _HEARTBEAT_KEY, _HEARTBEAT_TTL

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        _publish_heartbeat()

        mock_redis.setex.assert_called_once_with(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /opt/alphadivision/services/watchdog
python3 -m pytest tests/test_heartbeat.py -v
```
Expected: FAIL — `ImportError: cannot import name 'get_redis' from 'main'` (it isn't imported yet) or `_publish_heartbeat` not defined.

- [ ] **Step 3: Implement**

Read `/opt/alphadivision/services/watchdog/main.py` first.

Change the imports (currently):
```python
from shared.config import load_config
from deduplicator import fingerprint, is_suppressed, record_seen
from error_classifier import classify_error
from action_runner import run_action
from log_monitor import collect_errors
from notifier import send_notification
```
to:
```python
from shared.config import load_config
from shared.redis_client import get_redis
from deduplicator import fingerprint, is_suppressed, record_seen
from error_classifier import classify_error
from action_runner import run_action
from log_monitor import collect_errors
from notifier import send_notification
```

Insert these constants and function right after `log = logging.getLogger("watchdog")`:
```python
log = logging.getLogger("watchdog")

_HEARTBEAT_KEY = "heartbeat:watchdog"
_HEARTBEAT_TTL = 90        # seconds — refreshed every 60s so TTL never expires during normal operation
_HEARTBEAT_INTERVAL = 60   # seconds


def _publish_heartbeat() -> None:
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")
```

Then change `main()` (currently):
```python
def main() -> None:
    cfg = load_config()
    w = cfg["watchdog"]
    _add_file_handler(w["log_file"])
    log.info(f"Watchdog starting (model: {w['ollama_model']}, interval: {w['poll_interval_seconds']}s)")
    send_notification(f"[watchdog] \U0001f7e2 Watchdog started (model: {w['ollama_model']})")

    while True:
        try:
            run_once(cfg)
            # Reload config each cycle so dashboard changes take effect
            cfg = load_config()
        except Exception as exc:
            log.error(f"Poll cycle failed: {exc}")
        time.sleep(w["poll_interval_seconds"])
```
to:
```python
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
                log.error(f"Heartbeat failed: {exc}")
            last_heartbeat = now
        try:
            run_once(cfg)
            # Reload config each cycle so dashboard changes take effect
            cfg = load_config()
        except Exception as exc:
            log.error(f"Poll cycle failed: {exc}")
        time.sleep(w["poll_interval_seconds"])
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /opt/alphadivision/services/watchdog
python3 -m pytest tests/test_heartbeat.py -v
```
Expected: `1 passed`

- [ ] **Step 5: Confirm no existing watchdog tests broke**

```bash
cd /opt/alphadivision/services/watchdog
python3 -m pytest tests/ -v
```
Expected: all tests pass (existing count plus the 1 new test).

- [ ] **Step 6: Live-verify (restarts the real watchdog systemd service — safe, already done repeatedly this session)**

```bash
sudo bash /opt/alphadivision/services/watchdog/install.sh
sleep 5
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:watchdog
sudo systemctl is-active alphadivision-watchdog
```
Expected: `TTL heartbeat:watchdog` returns a positive integer (≤ 90); `is-active` returns `active`.

- [ ] **Step 7: Commit**

```bash
cd /opt/alphadivision
git add services/watchdog/main.py services/watchdog/tests/test_heartbeat.py
git commit -m "$(cat <<'EOF'
feat(watchdog): publish a heartbeat so the dashboard can monitor this service

Extends the existing heartbeat:<service> Redis pattern to the
watchdog, which had no dashboard visibility at all until now. Adds
the one missing import (shared.redis_client.get_redis) alongside the
same constants/function shape used by analysis/execution/alerts.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Add heartbeat publishing to services/research/main.py

**Files:**
- Modify: `services/research/main.py`
- Test: `services/research/tests/test_heartbeat.py`

**Interfaces:**
- Consumes: `log` (already defined via `log = get_logger("research")`). Does NOT currently import `threading`, `time`, or `shared.redis_client` — this task adds all three.
- Produces: `_publish_heartbeat()`, `_start_heartbeat_thread()`, `_HEARTBEAT_KEY = "heartbeat:research"`, `_HEARTBEAT_TTL`, `_HEARTBEAT_INTERVAL` — no other task consumes these.

**Note:** Unlike Tasks 1 and 2, this service has no main loop — gunicorn imports `main:app` directly. `_start_heartbeat_thread()` must be called at **module level** (not inside `if __name__ == "__main__":`) so it actually runs in production. This means it also runs during test collection (any test importing `main` triggers it) — this is expected; the thread's body is wrapped in try/except so an unmocked/unreachable Redis during tests just logs an error from a background daemon thread without affecting test results.

- [ ] **Step 1: Write the failing test**

Create `/opt/alphadivision/services/research/tests/test_heartbeat.py`:

```python
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import unittest
from unittest.mock import patch, MagicMock


class TestPublishHeartbeat(unittest.TestCase):
    @patch("main.get_redis")
    def test_publish_heartbeat_sets_correct_key_and_ttl(self, mock_get_redis):
        from main import _publish_heartbeat, _HEARTBEAT_KEY, _HEARTBEAT_TTL

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        _publish_heartbeat()

        mock_redis.setex.assert_called_once_with(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
sudo docker exec -w /app alphadivision-research-1 python -m unittest tests.test_heartbeat -v
```
Expected: FAIL — `ImportError: cannot import name '_publish_heartbeat' from 'main'` (or `get_redis` not yet imported).

- [ ] **Step 3: Implement**

Read `/opt/alphadivision/services/research/main.py` first.

Change the imports (currently):
```python
# services/research/main.py
import sys
import os
sys.path.insert(0, "/app")

import jinja2
import requests
from flask import Flask, render_template, jsonify, request
from shared.config import load_config
from shared.logger import get_logger
```
to:
```python
# services/research/main.py
import sys
import os
import threading
import time
sys.path.insert(0, "/app")

import jinja2
import requests
from flask import Flask, render_template, jsonify, request
from shared.config import load_config
from shared.logger import get_logger
from shared.redis_client import get_redis
```

Insert these constants and functions right after `log = get_logger("research")` (currently followed by `_DASHBOARD_URL = ...`):
```python
log = get_logger("research")

_HEARTBEAT_KEY = "heartbeat:research"
_HEARTBEAT_TTL = 90        # seconds — refreshed every 60s so TTL never expires during normal operation
_HEARTBEAT_INTERVAL = 60   # seconds


def _publish_heartbeat() -> None:
    r = get_redis()
    r.setex(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


def _heartbeat_loop() -> None:
    while True:
        try:
            _publish_heartbeat()
        except Exception as exc:
            log.error("Heartbeat failed: %s", exc)
        time.sleep(_HEARTBEAT_INTERVAL)


def _start_heartbeat_thread() -> None:
    threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat").start()


_DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8080")
```

Then, right after `app.jinja_loader = jinja2.ChoiceLoader([...])` block (before the `@app.context_processor` decorator), add the module-level call that actually starts the thread:
```python
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.FileSystemLoader("/app/shared/templates"),
])

_start_heartbeat_thread()

@app.context_processor
def _inject_nav():
```

- [ ] **Step 4: Run test to verify it passes**

```bash
sudo docker exec -w /app alphadivision-research-1 python -m unittest tests.test_heartbeat -v
```
Expected: `OK` (1 test passed)

- [ ] **Step 5: Confirm no existing research tests broke**

```bash
sudo docker exec -w /app alphadivision-research-1 python -m unittest discover -s tests -v
```
Expected: all existing tests plus the 1 new test pass (74 existing + 1 = 75, per the last full run recorded earlier this session — confirm the count is at least that, exact number may have grown from concurrent work on this repo).

- [ ] **Step 6: Live-verify (safe — the research container will restart to pick up the code change; it is not the `execution` service)**

```bash
sudo docker compose -f /opt/alphadivision/docker-compose.yml up -d --force-recreate research
sleep 5
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:research
curl -s -o /dev/null -w "research /health: %{http_code}\n" http://localhost:8081/health
```
Expected: `TTL heartbeat:research` returns a positive integer (≤ 90); `/health` returns `200`.

- [ ] **Step 7: Commit**

```bash
cd /opt/alphadivision
git add services/research/main.py services/research/tests/test_heartbeat.py
git commit -m "$(cat <<'EOF'
feat(research): publish a heartbeat so the dashboard can monitor this service

research has no main loop (gunicorn serves the Flask app directly),
so unlike ml/watchdog this adds a daemon background thread started at
module level, ticking on its own 60s timer independent of HTTP
traffic — publishing on every request would make "alive" really mean
"received a request recently," which is a noisy, misleading signal
for a low-traffic service like this one.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Add research/ml/watchdog to the dashboard's MONITORED_SERVICES

**Files:**
- Modify: `services/dashboard/service_status.py`

**Interfaces:**
- Consumes: `heartbeat:research`, `heartbeat:ml`, `heartbeat:watchdog` Redis keys (published by Tasks 1-3).
- Produces: nothing further downstream — this is the last task.

- [ ] **Step 1: Confirm the existing test suite is generic (no test changes needed)**

Read `/opt/alphadivision/services/dashboard/tests/test_service_status.py` and confirm every test asserts against `MONITORED_SERVICES` (imported from `service_status`) rather than a hardcoded literal list — e.g. `self.assertEqual(len(result), len(MONITORED_SERVICES))`, `self.assertEqual(names, MONITORED_SERVICES)`. If this holds (it does as of this plan being written), no test file changes are needed for this task — the existing suite will automatically cover the expanded list.

- [ ] **Step 2: Run the existing test suite to confirm current passing state**

```bash
sudo docker exec -w /app alphadivision-dashboard-1 python -m unittest tests.test_service_status -v
```
Expected: all existing tests pass (7 tests, per the suite read in Step 1).

- [ ] **Step 3: Implement**

Read `/opt/alphadivision/services/dashboard/service_status.py` first. Change:
```python
MONITORED_SERVICES = ["data", "analysis", "execution", "alerts"]
```
to:
```python
MONITORED_SERVICES = ["data", "analysis", "execution", "alerts", "research", "ml", "watchdog"]
```

- [ ] **Step 4: Run test to verify it passes with the expanded list**

```bash
sudo docker exec -w /app alphadivision-dashboard-1 python -m unittest tests.test_service_status -v
```
Expected: all tests still pass — `test_returns_entry_per_service`, `test_checks_correct_heartbeat_keys`, and `test_service_names_match_monitored_services` now exercise 7 services instead of 4, with no code changes needed since they were already written generically.

- [ ] **Step 5: Live-verify end to end**

```bash
sudo docker compose -f /opt/alphadivision/docker-compose.yml up -d --force-recreate dashboard
sleep 3
curl -s http://localhost:8080/api/overview | python3 -c "import sys, json; d = json.load(sys.stdin); print([s['name'] for s in d['services']])"
```
Expected: `['data', 'analysis', 'execution', 'alerts', 'research', 'ml', 'watchdog']`, and (assuming Tasks 1-3 already ran their live-verify steps so all three now have live heartbeats) every entry should show as alive on the actual dashboard overview page at `http://localhost:8080/`.

- [ ] **Step 6: Commit**

```bash
cd /opt/alphadivision
git add services/dashboard/service_status.py
git commit -m "$(cat <<'EOF'
feat(dashboard): monitor research/ml/watchdog in the Services checklist

Now that all three publish a heartbeat (prior 3 commits), one line
here makes the dashboard actually display their status. The existing
test suite already asserts generically against MONITORED_SERVICES, so
it required no changes to cover the expansion.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Post-Implementation Verification

After all 4 tasks are complete:

```bash
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:data
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:analysis
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:execution
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:alerts
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:research
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:ml
sudo docker exec alphadivision-redis-1 redis-cli TTL heartbeat:watchdog
curl -s http://localhost:8080/api/overview | python3 -c "import sys, json; d = json.load(sys.stdin); print([(s['name'], s['alive']) for s in d['services']])"
```

Expected: all 7 `TTL` calls return a positive integer, and the final line shows all 7 services with `alive: True`.
