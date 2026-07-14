# Dashboard Service Heartbeats — research/ml/watchdog — Design

**Date:** 2026-07-14
**Status:** Approved

---

## Problem

The dashboard's Services status checklist (`services/dashboard/service_status.py`'s `MONITORED_SERVICES`) only covers `data`, `analysis`, `execution`, and `alerts` — the four services that happen to already publish a Redis heartbeat. Three other long-running processes have no presence in any dashboard checklist at all:

- **`research`** — a user-facing Flask service (the `/research` and `/candidates` pages, backtest triggering). If it crashes, users hit errors with zero visibility from the dashboard that anything is wrong.
- **`ml`** — the nightly strategy-discovery pipeline. Partially covered today by the `ml_runs` table (shown on `/research`), which records whether the *last run* succeeded — but nothing today answers "is the process itself still alive right now."
- **`watchdog`** — the host-level systemd service that reads Docker logs and classifies errors via a local LLM. Entirely invisible today; if it dies silently, nothing else notices.

Separately, `ollama` is already checked (hourly, and immediately on provider switch, via `services/data/health_checker.py`) and shown in the dashboard's existing **API Health** panel — it doesn't belong in this change, since it's an external dependency (same category as Alpaca/Finnhub/FRED), not one of our own services.

`postgres` and `redis` are explicitly out of scope: neither fits the "service publishes its own heartbeat" pattern (Redis can't publish a heartbeat into itself without a circularity problem; Postgres isn't a Python process we control), and both already have a blunter existing signal — most other dashboard panels query them directly and fail loudly if either is down. Adding proper checks for these two is a different mechanism (an active ping, probably belonging in the API Health panel) and is left as separate future work.

---

## Goals

- `research`, `ml`, and `watchdog` appear in the dashboard's Services checklist with accurate alive/dead status.
- Every addition reuses the exact existing heartbeat pattern (`heartbeat:<service>` Redis key, `SETEX` with a 90s TTL refreshed every 60s) already used by `data`/`analysis`/`execution`/`alerts` — no new mechanism invented.

## Non-Goals

- Ollama, Postgres, or Redis checks (see Problem section above for why each is out of scope).
- Any change to how `ml_runs` history or the `api_health` table/panel work today — both are unaffected by this change.

---

## Design Decisions

**`research` and `ml` both need a background thread; only `watchdog` uses the in-loop pattern.** `research` is served by gunicorn (2 workers), which imports `main.py` as a WSGI app with no main loop at all, so it needs a daemon background thread, started at module level (so it runs under gunicorn *and* a direct `python main.py`), ticking on its own 60-second timer independent of HTTP traffic. The alternative (publish on every request via `@app.before_request`) was rejected: research is low-traffic, so "alive" would end up really meaning "received a request in the last 90s," producing a noisy, frequently-false "dead" signal during normal idle periods rather than reflecting whether the process is actually up.

`ml` was originally planned as an in-loop addition (it does have a `while True: ...` loop), but implementation revealed that loop calls `run_pipeline()` synchronously — a multi-minute blocking job (Alpaca fetches, Claude/Gemini codegen, backtest calls) — during which an in-loop heartbeat gate can't fire, so the TTL would expire and the dashboard would show `ml` as dead during every nightly run and for the first minutes after every restart. Caught in final review and fixed by converting `ml` to the same background-thread pattern as `research`, dropping the in-loop gate entirely.

**Two (or more) threads/workers independently writing the same heartbeat key is fine.** `SETEX` is idempotent — this just means the key gets refreshed slightly more often than the 60s baseline, which is harmless and matches the TTL headroom (90s TTL, refreshed at least every 60s) the existing pattern already assumes. This applies to `research`'s 2 gunicorn workers and would apply equally if `ml`'s single process were ever scaled to multiple replicas.

**`watchdog` needs one new import plus a real infrastructure fix, not just constants.** It already has the right loop shape (`while True: ...; time.sleep(w["poll_interval_seconds"])`) and the same `sys.path.insert(0, "/opt/alphadivision")` setup every other service uses to reach `shared/` — but unlike `ml`/`research` (both Docker Compose services on the same network as `redis`), `watchdog` runs as a **host systemd service** and can't resolve the compose-network `redis` hostname `.env` provides. Fixed by publishing Redis to the host on `127.0.0.1:6379` only (loopback, via a new `docker-compose.yml` `ports:` entry on the `redis` service) and giving the `watchdog` systemd unit its own `REDIS_URL` override via a second `EnvironmentFile=` (`services/watchdog/redis_override.env`, a new non-secret file) — Docker Compose services' `REDIS_URL=redis://redis:6379` is unaffected.

**Dashboard needs no test changes.** `services/dashboard/tests/test_service_status.py` already asserts everything (count, alive/dead logic, correct heartbeat key names, name-list equality) generically against `MONITORED_SERVICES` rather than a hardcoded literal — appending three names to that list is automatically covered by the existing suite.

---

## File Changes

**Modified:**
- `services/dashboard/service_status.py` — `MONITORED_SERVICES` gains `"research"`, `"ml"`, `"watchdog"`.
- `services/ml/pipeline.py` — new heartbeat constants + `_publish_heartbeat()` + `_heartbeat_loop()`/`_start_heartbeat_thread()`, started as a background daemon thread from `main()` (not in-loop — see Design Decisions).
- `services/watchdog/main.py` — same constants/function shape; adds one new import (`shared.redis_client.get_redis`), called from the existing `while True` loop.
- `services/watchdog/watchdog.service` — adds a second `EnvironmentFile=` line (after `.env`) pointing at the new override file.
- `services/research/main.py` — new heartbeat constants + a heartbeat-publish function + a daemon thread starting it, at module level. New imports: `threading`, `time`, `shared.redis_client.get_redis`.
- `services/research/requirements.txt` — adds `redis==5.0.4` (missing entirely before this change; required transitively by `shared.redis_client`, which `research/main.py` now imports at module level).
- `docker-compose.yml` — adds `ports: ["127.0.0.1:6379:6379"]` to the `redis` service (loopback-only, so `watchdog` can reach it from the host).
- `services/ml/tests/test_pipeline_heartbeat.py` — new test file covering `_publish_heartbeat()` (mock `get_redis`, assert correct key/TTL args).
- `services/watchdog/tests/test_heartbeat.py` — new test covering the same shape.
- `services/research/tests/test_heartbeat.py` — new test covering the heartbeat-publish function, structured so the single-iteration publish logic is unit-testable independent of the `while True`/`sleep(60)` wrapper (i.e., the sleep loop is a thin wrapper around a separately-callable, separately-tested publish function — matching how `analysis`/`execution`/`alerts` already separate `_publish_heartbeat()` from their own main loops).

**Added:**
- `services/watchdog/redis_override.env` — non-secret, committed file containing only `REDIS_URL=redis://127.0.0.1:6379`, loaded by `watchdog.service` as a second `EnvironmentFile=` to override just this one unit's copy of the variable.

**Unchanged:**
- `services/dashboard/tests/test_service_status.py` (already generic, see Design Decisions).
- All templates, all other service files.

---

## Testing / Verification Approach

Every service in this repo has its own `tests/` directory with unit tests per `CLAUDE.md`'s testing conventions, and all three touched services (`ml`, `watchdog`, `research`) already have one. New tests follow the existing per-service pattern: mock `get_redis`, assert the heartbeat function calls `setex` with the exact expected key (`heartbeat:research` / `heartbeat:ml` / `heartbeat:watchdog`) and TTL, without invoking any real Redis connection or the surrounding sleep loop. This matches how `services/analysis/main.py`'s own `_publish_heartbeat` would be tested if it had a dedicated test (it doesn't currently, but the new tests here follow the same shape its code already uses).

Live verification: after implementation, confirm each new heartbeat actually appears via `redis-cli TTL heartbeat:research` / `heartbeat:ml` / `heartbeat:watchdog` returning a positive value within ~60s of each service (re)starting, and confirm the dashboard's `/api/overview` (or overview page) now lists all 7 services.

---

## Out of Scope

- Ollama, Postgres, Redis checks (see Problem section).
- Any UI/template changes to how the Services panel renders — it already iterates over whatever `get_service_statuses()` returns, so no template change is needed for the new entries to show up correctly.
