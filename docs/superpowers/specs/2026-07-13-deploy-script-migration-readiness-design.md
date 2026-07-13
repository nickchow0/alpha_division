# Deploy Script Migration-Readiness Fixes — Design

**Date:** 2026-07-13
**Status:** Approved

---

## Problem

A live debugging session (fixing the `/research` 500, the ML pipeline proxy gap, and a fully-blind watchdog) surfaced that `deploy.sh` — the script meant to make a fresh server migration reproducible — is significantly out of sync with what's actually required to run this system:

- It installs the **legacy** watchdog (`watchdog/`, a simple Redis-heartbeat restarter with no AI/log-reading), not `services/watchdog/` (the LLM-based log classifier actually running in production and covered by `docs/superpowers/specs/2026-07-13-local-llm-watchdog-design.md`).
- It never installs Ollama or pulls any models, despite `analysis`, `ml`, and the watchdog all depending on it per `config.toml`.
- It never applies `db/migrations/*.sql` — only `db/schema.sql` auto-runs (via Postgres's `docker-entrypoint-initdb.d`), and only on a brand-new data volume. This is the exact gap that silently broke `/research` (missing `strategies`/`backtest_runs`/`backtest_trades` tables) until fixed by hand mid-session.
- Because the whole script runs as root (`git clone`, docker builds, etc. all executed as root), every file it touches ends up root-owned — while the watchdog and day-to-day operator work run as `ubuntu`. This produced a string of "permission denied" surprises this session: `.git/` itself, the watchdog's state file, and it's why `db/*.sql` still shows permission-denied under `git status` on the current host today.
- Nothing adds the deploying user to the `docker` group, so the watchdog — which shells out to `docker compose logs` — has been silently blind since it was first deployed (confirmed: it never once successfully read a log line until fixed this session).

A migration to a new server using `deploy.sh` as it stands today would reproduce every one of these bugs from scratch.

---

## Goals

- `deploy.sh` produces a fully working system unattended, on a brand-new Ubuntu box, matching the production system's actual (fixed) behavior — not its historical bugs.
- Every new step is idempotent and safe to re-run, consistent with the script's existing design (see `2026-05-16-deploy-script-design.md`).
- Ollama model selection stays in sync with `config.toml` automatically (no hardcoded model list to go stale).

## Non-Goals

- nginx reverse proxy / SSL/TLS setup — inherently domain-specific (new DNS record, certbot validation against a not-yet-provisioned domain), stays a manual post-deploy step. (The original 2026-05-16 spec already scoped SSL out, historically via Tailscale; this doesn't change that.)
- A migrations-tracking table (`schema_migrations`) — the existing 4 migrations are all `IF NOT EXISTS`-guarded; re-running the whole directory every deploy is simpler and consistent with how they were already written.
- Firewall/security-list automation beyond what `scripts/provision-oracle.sh` already does.

---

## Design Decisions

**Legacy watchdog is deleted, not just bypassed.** It's a strict functional subset of `services/watchdog/` (heartbeat-restart vs. full log-based LLM classification), and running both risks double-remediation — e.g. both independently restarting the same container. Deleting removes the ambiguity entirely rather than leaving a second, unused-but-present implementation to confuse future readers.

**Ollama models are read from `config.toml` at deploy time, not hardcoded.** `[analysis].ollama_model`, `[ml].ollama_codegen_model`, and `[watchdog].ollama_model` are already the single source of truth for model selection (per `CLAUDE.md`'s config conventions). Parsing them keeps `deploy.sh` correct automatically if models are ever changed.

**`OLLAMA_MODELS` is deliberately left at Ollama's own default path, not overridden to something under `/home/ubuntu`.** The current production host's ownership bug (models directory owned by `ubuntu`, but the `ollama` systemd service runs as its own dedicated `ollama` user) only exists because `OLLAMA_MODELS` was pointed at a custom, `ubuntu`-owned path. The official Ollama installer creates its own user and a self-consistently-owned default model directory; not touching that setting avoids reproducing the bug rather than chowning around it after the fact. `OLLAMA_HOST=0.0.0.0:11434` is still overridden — that one's required so Docker containers can reach Ollama via the bridge IP; Ollama's default bind is loopback-only.

**Whole-repo chown to the deploying non-root user, not per-file patches.** Since `deploy.sh` runs entirely as root, everything it creates (the clone, `.git`, build state) ends up root-owned, while the watchdog and interactive operator work both run as `ubuntu`. Rather than chasing down each file that turns out to need `ubuntu` ownership (as happened three separate times this session — `.git`, the watchdog state file, and still-outstanding on `db/*`), one `chown -R` after the clone step fixes the whole class of bug up front.

**Migration failures are hard errors; Ollama/model-pull failures are warnings.** The core trading loop works fine on Claude/Gemini without Ollama (it's the configured *default* provider per `config.toml`), so a failed model pull or missing Ollama installation shouldn't block the rest of the deploy. A migration that fails to apply for a real reason (not just "already exists," since those are guarded) means the schema is broken and nothing downstream can be trusted — that must stop the script.

---

## File Changes

**Deleted:**
- `watchdog/` (`watchdog.py`, `install.sh`, `uninstall.sh`, `alphadivision-watchdog.service`, `README.md`, `tests/`)

**Added:**
- `services/watchdog/install.sh` — mirrors the deleted legacy script's UX (preflight checks, idempotent install/restart, status printout) but installs the real thing: `pip install -r services/watchdog/requirements.txt`, copies the already-correct `services/watchdog/watchdog.service` (which already declares `Wants=ollama.service`) to `/etc/systemd/system/alphadivision-watchdog.service`.
- `services/watchdog/uninstall.sh` — mirrors the deleted legacy script's teardown.

**Changed:**
- `deploy.sh` — new steps described below; step 7 (watchdog) repointed at `services/watchdog/install.sh`.
- `docs/superpowers/specs/2026-05-16-deploy-script-design.md` — updated after implementation to reflect the new step list (per `CLAUDE.md`'s "update the design spec to reflect what was built").

---

## `deploy.sh` — New Step-by-Step Flow

1. *(existing)* Preflight: assert root, warn on non-Ubuntu.
2. *(existing)* Install Docker, Python3, pip3 if missing.
3. **New:** `usermod -aG docker "${SUDO_USER:-ubuntu}"` — the invoking user needs docker-group access or the watchdog stays blind, matching today's bug exactly. Warns (doesn't fail) if the target user doesn't exist.
4. *(existing)* Clone or pull the repo.
5. **New:** `chown -R "${SUDO_USER:-ubuntu}:${SUDO_USER:-ubuntu}" "$INSTALL_DIR"` — see Design Decisions above. Hard error on failure (nothing downstream should be trusted if this fails).
6. *(existing)* `.env` check.
7. *(existing)* Backup directory setup.
8. *(existing)* `docker compose build` + `docker compose up -d`.
9. **New:** Wait for Postgres healthy (reuse the existing poll-loop pattern from the health-check step), then apply every file in `db/migrations/*.sql` in sorted order via `docker compose exec -T postgres psql`. Hard error on genuine failure.
10. **New:** Install Ollama if not present (official installer: `curl -fsSL https://ollama.com/install.sh | sh`), add a systemd override setting only `OLLAMA_HOST=0.0.0.0:11434`. Warn (don't fail) on installer failure — Ollama is an enhancement, not a hard dependency of the core trading loop.
11. **New:** Parse `ollama_model` / `ollama_codegen_model` / `[watchdog].ollama_model` out of `config.toml`, dedupe, and `ollama pull` each. Warn (don't fail) per-model on pull failure.
12. *(changed)* Install watchdog via `services/watchdog/install.sh` instead of the deleted legacy path.
13. *(existing)* Backup cron.
14. *(existing)* Dashboard health check (non-fatal warn on timeout, unchanged).

---

## Error Handling Summary

| Step | Failure behavior |
|---|---|
| docker-group add | Warn, continue |
| Repo chown | **Hard error** |
| DB migrations | **Hard error** on real failure (not "already exists") |
| Ollama install | Warn, continue |
| Model pulls | Warn per-model, continue |
| Watchdog install | *(unchanged — already hard-fails today via `set -euo pipefail` if `install.sh` errors)* |

Consistent with the script's existing convention: `set -euo pipefail` makes everything fatal by default; steps that are explicitly allowed to fail wrap their commands with `|| warn "..."`.

---

## Testing / Verification Approach

No bash test framework exists in this repo (verified — no bats/shunit2, and the original 2026-05-16 spec doesn't mention one). Given that, verification for this change is:

1. `bash -n deploy.sh` — syntax check the whole script.
2. Manual trace of the new/changed logic against this exact repo's structure (`config.toml` keys, `db/migrations/` contents, `services/watchdog/` layout).
3. Since the current host is already fully configured (Ollama installed, models pulled, migrations applied, docker-group membership set, repo already `ubuntu`-owned), the new idempotent steps can be safely run *against this live host* to directly confirm they correctly no-op rather than error or redundantly redo work — without needing to provision a throwaway VM via `scripts/provision-oracle.sh` (real cloud cost, not necessary to validate shell logic).

A full clean-VM run of `deploy.sh` end-to-end is out of scope for this change's verification (would require provisioning a real, billed VM) — the above gives confidence in the new logic without that cost.

---

## Out of Scope (unchanged from original spec)

- nginx / SSL/TLS setup
- Firewall configuration beyond `scripts/provision-oracle.sh`
- `.env` generation or secret rotation
- Multi-VM or containerized deployment
