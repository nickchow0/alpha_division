# Deploy Script Migration-Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `deploy.sh` produce a fully working AlphaDivision instance unattended on a brand-new server — installing the real watchdog (not the legacy one), Ollama + its models, and applying DB migrations — instead of silently reproducing the bugs fixed in the 2026-07-13 debugging session.

**Architecture:** All changes live in `deploy.sh` (new idempotent steps inserted into the existing numbered-section script) plus a new `services/watchdog/{install,uninstall}.sh` pair that replaces the deleted legacy `watchdog/` directory. No new services, no new runtime dependencies beyond what a handful of `pip3 install` calls already provide.

**Tech Stack:** bash (`set -euo pipefail`), Docker Compose, systemd, `psql` via `docker compose exec`, Python 3 + `tomllib`/`tomli` for parsing `config.toml`.

## Global Constraints

- Every new step must be idempotent and safe to re-run (per `docs/superpowers/specs/2026-05-16-deploy-script-design.md`, which this plan extends).
- Migration and repo-ownership failures are hard errors (`error()`, exits via `set -euo pipefail`); Ollama install and model-pull failures are warnings (`warn()`, script continues) — per `docs/superpowers/specs/2026-07-13-deploy-script-migration-readiness-design.md`.
- `OLLAMA_MODELS` is never overridden to a custom path — only `OLLAMA_HOST` is set, and only if no override file already exists (never clobber an existing host-specific override).
- No bash test framework exists in this repo. Verification for every task is: `bash -n deploy.sh` (syntax) + running the exact new command(s) against the live host (already fully configured, per the design's approved testing approach) to confirm idempotent/correct behavior, with exact expected output given.
- This is a live production host (`trade.submarinedivision.com`) — no task step may restart `docker compose up -d` for the whole stack, rebuild images, or touch the `execution` service.

---

## File Structure

- **Delete:** `watchdog/` (entire directory: `watchdog.py`, `install.sh`, `uninstall.sh`, `alphadivision-watchdog.service`, `README.md`, `tests/`)
- **Create:** `services/watchdog/install.sh` — installs the real watchdog (mirrors the deleted legacy script's UX)
- **Create:** `services/watchdog/uninstall.sh` — mirrors the deleted legacy script's teardown
- **Modify:** `deploy.sh` — 6 new/changed sections (docker group, repo chown, watchdog path, DB migrations, Ollama install, Ollama model pulls)
- **Modify:** `docs/superpowers/specs/2026-05-16-deploy-script-design.md` — updated to document the new steps once implemented

---

### Task 1: Add Docker group membership step to deploy.sh

**Files:**
- Modify: `deploy.sh` (insert new section before the `# ── 3. Repo setup` header)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing other tasks depend on directly, but must land before Task 6 (Watchdog install) conceptually — order doesn't matter for correctness since Task 6 doesn't re-verify group membership, it just needs it to actually be true before the watchdog service starts. Since this task lands early in the script (before repo clone even), it's satisfied regardless of task execution order.

- [ ] **Step 1: Read the current file and locate the anchor**

Run: `sed -n '75,92p' /opt/alphadivision/deploy.sh`

Expected output (the pip3 check block, ending right before the Repo setup header):
```bash
if ! command -v pip3 &>/dev/null; then
    info "Installing pip3..."
    apt-get install -y python3-pip
    info "pip3 installed."
else
    info "pip3 already installed — skipping."
fi

# ── 3. Repo setup ─────────────────────────────────────────────────────────────

info "Setting up repository..."
```

- [ ] **Step 2: Insert the new section**

Use the Edit tool on `/opt/alphadivision/deploy.sh` with:

old_string:
```
if ! command -v pip3 &>/dev/null; then
    info "Installing pip3..."
    apt-get install -y python3-pip
    info "pip3 installed."
else
    info "pip3 already installed — skipping."
fi

# ── 3. Repo setup ─────────────────────────────────────────────────────────────
```

new_string:
```
if ! command -v pip3 &>/dev/null; then
    info "Installing pip3..."
    apt-get install -y python3-pip
    info "pip3 installed."
else
    info "pip3 already installed — skipping."
fi

# ── 2b. Docker group membership ───────────────────────────────────────────────

info "Ensuring deploy user has Docker socket access..."

DEPLOY_USER="${SUDO_USER:-ubuntu}"

if id "$DEPLOY_USER" &>/dev/null; then
    if id -nG "$DEPLOY_USER" | grep -qw docker; then
        info "$DEPLOY_USER already in docker group — skipping."
    else
        usermod -aG docker "$DEPLOY_USER"
        info "$DEPLOY_USER added to docker group (takes effect for new processes, e.g. the watchdog systemd service started later in this script)."
    fi
else
    warn "User '$DEPLOY_USER' not found — skipping docker group setup. Add it manually: sudo usermod -aG docker <user>"
fi

# ── 3. Repo setup ─────────────────────────────────────────────────────────────
```

- [ ] **Step 3: Syntax-check the script**

Run: `bash -n /opt/alphadivision/deploy.sh`
Expected: no output, exit code 0

- [ ] **Step 4: Verify the new step's logic against the live host**

Run:
```bash
sudo bash -c '
DEPLOY_USER="${SUDO_USER:-ubuntu}"
if id "$DEPLOY_USER" &>/dev/null; then
    if id -nG "$DEPLOY_USER" | grep -qw docker; then
        echo "SKIP: $DEPLOY_USER already in docker group"
    else
        echo "WOULD ADD: $DEPLOY_USER to docker group"
    fi
else
    echo "WARN: user not found"
fi
'
```
Expected: `SKIP: ubuntu already in docker group` (the docker group was already added to `ubuntu` earlier this session — this confirms the idempotent skip path is correct without needing to actually re-run `usermod`).

- [ ] **Step 5: Commit**

```bash
cd /opt/alphadivision
git add deploy.sh
git commit -m "$(cat <<'EOF'
fix(deploy): add deploy user to docker group

The watchdog shells out to `docker compose logs` and silently returns
zero errors (not an exception) if that fails — it was blind since
first deployment because the running user was never in the docker
group. deploy.sh now adds the deploying user before the watchdog
starts.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add whole-repo chown step to deploy.sh

**Files:**
- Modify: `deploy.sh` (insert new section before the `# ── 4. .env check` header)

**Interfaces:**
- Consumes: `$DEPLOY_USER` (defined in Task 1, same file, earlier in execution order).
- Produces: nothing other tasks depend on directly.

- [ ] **Step 1: Read the current file and locate the anchor**

Run: `sed -n '/# ── 3. Repo setup/,/# ── 4\. \.env check/p' /opt/alphadivision/deploy.sh`

Expected output ends with:
```bash
else
    info "Repository exists — pulling latest changes..."
    git -C "$INSTALL_DIR" pull
    info "Repository updated."
fi

# ── 4. .env check ─────────────────────────────────────────────────────────────
```

- [ ] **Step 2: Insert the new section**

Use the Edit tool on `/opt/alphadivision/deploy.sh` with:

old_string:
```
else
    info "Repository exists — pulling latest changes..."
    git -C "$INSTALL_DIR" pull
    info "Repository updated."
fi

# ── 4. .env check ─────────────────────────────────────────────────────────────
```

new_string:
```
else
    info "Repository exists — pulling latest changes..."
    git -C "$INSTALL_DIR" pull
    info "Repository updated."
fi

# ── 3b. Repo ownership ────────────────────────────────────────────────────────

info "Setting repo ownership to $DEPLOY_USER..."

if id "$DEPLOY_USER" &>/dev/null; then
    chown -R "$DEPLOY_USER:$DEPLOY_USER" "$INSTALL_DIR" || \
        error "Failed to chown $INSTALL_DIR to $DEPLOY_USER — check the user exists and has a matching group."
    info "Repo ownership set to $DEPLOY_USER:$DEPLOY_USER."
else
    error "User '$DEPLOY_USER' not found — cannot set repo ownership. Create the user first or set SUDO_USER correctly."
fi

# ── 4. .env check ─────────────────────────────────────────────────────────────
```

- [ ] **Step 3: Syntax-check the script**

Run: `bash -n /opt/alphadivision/deploy.sh`
Expected: no output, exit code 0

- [ ] **Step 4: Verify the new step's logic against the live host**

Run:
```bash
sudo chown -R ubuntu:ubuntu /opt/alphadivision && echo "OK: chown succeeded, exit $?"
git -C /opt/alphadivision status --porcelain | head -5
```
Expected: `OK: chown succeeded, exit 0`. The `git status` line confirms the repo is still in a clean, working state after the chown (no spurious changes) — this is the same command already run manually earlier this session, now folded into the script.

- [ ] **Step 5: Commit**

```bash
cd /opt/alphadivision
git add deploy.sh
git commit -m "$(cat <<'EOF'
fix(deploy): chown repo to the deploy user after clone

deploy.sh runs entirely as root, so every file it creates (the clone
itself, .git, build state) ends up root-owned while the watchdog and
day-to-day operator work both run as the deploy user. This produced
three separate permission-denied surprises this session (.git, the
watchdog state file, and still-outstanding on db/*.sql). One chown
after the clone step fixes the whole class of bug up front.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Replace legacy watchdog with services/watchdog/

**Files:**
- Delete: `watchdog/` (entire directory)
- Create: `services/watchdog/install.sh`
- Create: `services/watchdog/uninstall.sh`
- Modify: `deploy.sh` (the `bash "$INSTALL_DIR/watchdog/install.sh"` line inside the `# ── 7. Watchdog` section)

**Interfaces:**
- Consumes: `services/watchdog/watchdog.service` (already exists, unchanged), `services/watchdog/requirements.txt` (already exists, unchanged), `services/watchdog/main.py` (already exists, unchanged).
- Produces: `services/watchdog/install.sh` and `uninstall.sh`, which Task 1/2/4/5/6 don't depend on but which `deploy.sh`'s Watchdog section now calls.

- [ ] **Step 1: Delete the legacy watchdog directory**

```bash
cd /opt/alphadivision
git rm -r watchdog/
```

Expected: lists every file under `watchdog/` as removed (`watchdog.py`, `install.sh`, `uninstall.sh`, `alphadivision-watchdog.service`, `README.md`, `tests/__init__.py`, `tests/test_watchdog.py`).

- [ ] **Step 2: Create services/watchdog/install.sh**

Write to `/opt/alphadivision/services/watchdog/install.sh`:

```bash
#!/usr/bin/env bash
# install.sh — Install and start the AlphaDivision Watchdog as a systemd service.
#
# Usage (run as root or with sudo on the Oracle VM):
#   sudo bash /opt/alphadivision/services/watchdog/install.sh
#
# What it does:
#   1. Checks prerequisites (Python 3, pip3, systemd, Docker)
#   2. Installs Python dependencies system-wide
#   3. Installs the systemd service file
#   4. Enables the service (auto-start on boot)
#   5. Starts the service and prints its status

set -euo pipefail

SERVICE_NAME="alphadivision-watchdog"
WATCHDOG_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="$WATCHDOG_DIR/watchdog.service"
INSTALL_DIR="$(cd "$WATCHDOG_DIR/../.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── 1. Preflight checks ────────────────────────────────────────────────────────

[[ $EUID -eq 0 ]] || error "Run this script as root: sudo bash $0"

command -v python3 >/dev/null || error "python3 not found — install it first"
command -v pip3    >/dev/null || error "pip3 not found — install it first"
command -v docker  >/dev/null || error "docker not found — install Docker first"
command -v systemctl >/dev/null || error "systemctl not found — this script requires systemd"

[[ -f "$SERVICE_FILE" ]] || error "Service file not found: $SERVICE_FILE"
[[ -f "$WATCHDOG_DIR/main.py" ]] || \
    error "main.py not found at $WATCHDOG_DIR/main.py — deploy the repo first"
[[ -f "$INSTALL_DIR/.env" ]] || \
    error ".env not found at $INSTALL_DIR/.env — copy .env.example and fill in your secrets"

info "Preflight checks passed."

# ── 2. Python dependencies ─────────────────────────────────────────────────────

info "Installing Python dependencies..."
pip3 install --quiet -r "$WATCHDOG_DIR/requirements.txt"
info "Python dependencies installed."

# ── 3. Install service file ────────────────────────────────────────────────────

info "Installing systemd service file to $SYSTEMD_DIR/$SERVICE_NAME.service ..."
cp "$SERVICE_FILE" "$SYSTEMD_DIR/$SERVICE_NAME.service"
chmod 644 "$SYSTEMD_DIR/$SERVICE_NAME.service"

systemctl daemon-reload
info "systemd daemon reloaded."

# ── 4. Enable + start ─────────────────────────────────────────────────────────

if systemctl is-active --quiet "$SERVICE_NAME"; then
    warn "Service is already running — restarting to pick up any changes..."
    systemctl restart "$SERVICE_NAME"
else
    systemctl enable "$SERVICE_NAME"
    systemctl start  "$SERVICE_NAME"
fi

# ── 5. Status ─────────────────────────────────────────────────────────────────

echo ""
systemctl status "$SERVICE_NAME" --no-pager --lines=10
echo ""
info "Done. Useful commands:"
echo "  View logs:    journalctl -u $SERVICE_NAME -f"
echo "  Stop:         sudo systemctl stop $SERVICE_NAME"
echo "  Disable:      sudo bash $WATCHDOG_DIR/uninstall.sh"
```

Make it executable: `chmod +x /opt/alphadivision/services/watchdog/install.sh`

- [ ] **Step 3: Create services/watchdog/uninstall.sh**

First read the legacy version for reference:

Run: `git show HEAD~1:watchdog/uninstall.sh` (from before the Task 3 Step 1 deletion — if this fails because the deletion was already committed by the time this step runs, use `git log --diff-filter=D --oneline -- watchdog/uninstall.sh` to find the commit before deletion, then `git show <that-commit>:watchdog/uninstall.sh`)

Write to `/opt/alphadivision/services/watchdog/uninstall.sh`, adapting paths from the legacy version:

```bash
#!/usr/bin/env bash
# uninstall.sh — Stop and remove the AlphaDivision Watchdog systemd service.
#
# Usage (run as root or with sudo):
#   sudo bash /opt/alphadivision/services/watchdog/uninstall.sh

set -euo pipefail

SERVICE_NAME="alphadivision-watchdog"
SYSTEMD_DIR="/etc/systemd/system"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || error "Run this script as root: sudo bash $0"

if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Stopping $SERVICE_NAME..."
    systemctl stop "$SERVICE_NAME"
else
    info "$SERVICE_NAME is not running — skipping stop."
fi

if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    info "Disabling $SERVICE_NAME..."
    systemctl disable "$SERVICE_NAME"
else
    info "$SERVICE_NAME is not enabled — skipping disable."
fi

if [[ -f "$SYSTEMD_DIR/$SERVICE_NAME.service" ]]; then
    info "Removing service file..."
    rm "$SYSTEMD_DIR/$SERVICE_NAME.service"
    systemctl daemon-reload
else
    info "Service file already absent — skipping."
fi

info "Watchdog uninstalled."
```

Make it executable: `chmod +x /opt/alphadivision/services/watchdog/uninstall.sh`

- [ ] **Step 4: Update deploy.sh to call the new install path**

Read the current Watchdog section:

Run: `sed -n '/# ── 7\. Watchdog/,/# ── 8\. Backup cron/p' /opt/alphadivision/deploy.sh`

Expected:
```bash
# ── 7. Watchdog ───────────────────────────────────────────────────────────────

info "Installing watchdog service..."
bash "$INSTALL_DIR/watchdog/install.sh"
info "Watchdog installed."

# ── 8. Backup cron ────────────────────────────────────────────────────────────
```

Use the Edit tool on `/opt/alphadivision/deploy.sh` with:

old_string:
```
info "Installing watchdog service..."
bash "$INSTALL_DIR/watchdog/install.sh"
info "Watchdog installed."
```

new_string:
```
info "Installing watchdog service..."
bash "$INSTALL_DIR/services/watchdog/install.sh"
info "Watchdog installed."
```

- [ ] **Step 5: Syntax-check the script**

Run: `bash -n /opt/alphadivision/deploy.sh`
Expected: no output, exit code 0

Run: `bash -n /opt/alphadivision/services/watchdog/install.sh`
Expected: no output, exit code 0

Run: `bash -n /opt/alphadivision/services/watchdog/uninstall.sh`
Expected: no output, exit code 0

- [ ] **Step 6: Verify the new install.sh works against the live host**

Run: `sudo bash /opt/alphadivision/services/watchdog/install.sh`

Expected: prints `[INFO]  Preflight checks passed.`, `[INFO]  Python dependencies installed.`, `[WARN]  Service is already running — restarting to pick up any changes...` (since the watchdog is already running from earlier this session), followed by `systemctl status` output showing `Active: active (running)`.

Confirm the service is genuinely healthy afterward:

Run: `sudo systemctl is-active alphadivision-watchdog`
Expected: `active`

- [ ] **Step 7: Verify the legacy directory is gone and nothing else references it**

Run: `ls /opt/alphadivision/watchdog 2>&1`
Expected: `ls: cannot access '/opt/alphadivision/watchdog': No such file or directory`

Run: `grep -rn "INSTALL_DIR/watchdog/\|/opt/alphadivision/watchdog/" /opt/alphadivision/deploy.sh /opt/alphadivision/README.md 2>&1`
Expected: no matches (empty output) — confirms nothing else in the repo still points at the deleted legacy path.

- [ ] **Step 8: Commit**

```bash
cd /opt/alphadivision
git add -A watchdog/ services/watchdog/install.sh services/watchdog/uninstall.sh deploy.sh
git commit -m "$(cat <<'EOF'
fix(deploy,watchdog): replace legacy heartbeat watchdog with services/watchdog

watchdog/ was a simple Redis-heartbeat restarter with no log reading
or AI classification — a strict subset of services/watchdog/ (the
LLM-based log classifier that's actually been running in production).
deploy.sh installed the legacy one; running both risks double
remediation. Deletes the legacy implementation, adds install/uninstall
scripts for the real one, and repoints deploy.sh at it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Add DB migration runner to deploy.sh

**Files:**
- Modify: `deploy.sh` (insert new section before the `# ── 7. Watchdog` header)

**Interfaces:**
- Consumes: `$ENV_FILE` (already defined earlier in the script, `# ── 4. .env check` section), `$INSTALL_DIR` (already defined).
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Read the current file and locate the anchor**

Run: `sed -n '/# ── 6\. Docker services/,/# ── 7\. Watchdog/p' /opt/alphadivision/deploy.sh`

Expected:
```bash
# ── 6. Docker services ────────────────────────────────────────────────────────

info "Building Docker images..."
docker compose -f "$INSTALL_DIR/docker-compose.yml" build

info "Starting Docker services..."
docker compose -f "$INSTALL_DIR/docker-compose.yml" up -d

info "Docker services started."

# ── 7. Watchdog ───────────────────────────────────────────────────────────────
```

- [ ] **Step 2: Insert the new section**

Use the Edit tool on `/opt/alphadivision/deploy.sh` with:

old_string:
```
info "Docker services started."

# ── 7. Watchdog ───────────────────────────────────────────────────────────────
```

new_string:
```
info "Docker services started."

# ── 6b. Database migrations ───────────────────────────────────────────────────

info "Waiting for Postgres to become healthy..."

POSTGRES_USER_VAL=$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | head -1 | cut -d '=' -f2-)
[[ -n "$POSTGRES_USER_VAL" ]] || error "POSTGRES_USER not found in $ENV_FILE"

PG_TIMEOUT=60
PG_ELAPSED=0
until docker compose -f "$INSTALL_DIR/docker-compose.yml" exec -T postgres \
        pg_isready -U "$POSTGRES_USER_VAL" &>/dev/null; do
    sleep 2
    PG_ELAPSED=$((PG_ELAPSED + 2))
    if [[ $PG_ELAPSED -ge $PG_TIMEOUT ]]; then
        error "Postgres did not become healthy within ${PG_TIMEOUT}s — check: docker compose -f $INSTALL_DIR/docker-compose.yml logs postgres"
    fi
done
info "Postgres healthy."

info "Applying database migrations..."
for MIGRATION in "$INSTALL_DIR"/db/migrations/*.sql; do
    [[ -e "$MIGRATION" ]] || continue
    info "  Applying $(basename "$MIGRATION")..."
    docker compose -f "$INSTALL_DIR/docker-compose.yml" exec -T postgres \
        psql -U "$POSTGRES_USER_VAL" -d alphadivision -v ON_ERROR_STOP=1 \
        < "$MIGRATION" || error "Migration $(basename "$MIGRATION") failed — check: cat $MIGRATION"
done
info "Migrations applied."

# ── 7. Watchdog ───────────────────────────────────────────────────────────────
```

- [ ] **Step 3: Syntax-check the script**

Run: `bash -n /opt/alphadivision/deploy.sh`
Expected: no output, exit code 0

- [ ] **Step 4: Verify the new step's logic against the live host**

Run:
```bash
cd /opt/alphadivision
POSTGRES_USER_VAL=$(grep -E '^POSTGRES_USER=' .env | head -1 | cut -d '=' -f2-)
echo "Extracted user: $POSTGRES_USER_VAL"
docker compose -f docker-compose.yml exec -T postgres pg_isready -U "$POSTGRES_USER_VAL"
```
Expected: `Extracted user: alphadivision` followed by `/var/run/postgresql:5432 - accepting connections`

Then verify the migration loop is safely idempotent (all 4 migrations already applied on this host):
```bash
for MIGRATION in db/migrations/*.sql; do
    echo "Applying $(basename "$MIGRATION")..."
    docker compose -f docker-compose.yml exec -T postgres \
        psql -U "$POSTGRES_USER_VAL" -d alphadivision -v ON_ERROR_STOP=1 \
        < "$MIGRATION"
done
```
Expected: each migration prints `ALTER TABLE`/`CREATE TABLE`/`CREATE INDEX` (all `IF NOT EXISTS`-guarded, so no errors even though already applied), exit code 0 for all four.

- [ ] **Step 5: Commit**

```bash
cd /opt/alphadivision
git add deploy.sh
git commit -m "$(cat <<'EOF'
fix(deploy): auto-apply db/migrations/ on every deploy

Only db/schema.sql auto-runs (via Postgres's docker-entrypoint-initdb.d,
and only on a brand-new data volume) — the four numbered migrations
were never applied automatically. This is the exact gap that silently
broke /research (missing strategies/backtest_runs/backtest_trades)
until fixed by hand. All four are IF NOT EXISTS-guarded, so re-running
them every deploy is safe.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Add Ollama install + OLLAMA_HOST override step to deploy.sh

**Files:**
- Modify: `deploy.sh` (insert new section before the `# ── 7. Watchdog` header — lands after Task 4's migrations section since that task's insertion happens first and shares the same anchor)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the `ollama` command being available on `$PATH`, consumed by Task 6.

- [ ] **Step 1: Read the current file and locate the anchor**

Run: `sed -n '/# ── 7\. Watchdog/p' /opt/alphadivision/deploy.sh`
Expected: `# ── 7. Watchdog ───────────────────────────────────────────────────────────────` (still present and unique — Task 4 only inserted content before it, didn't change the header text itself)

- [ ] **Step 2: Insert the new section**

Use the Edit tool on `/opt/alphadivision/deploy.sh` with:

old_string:
```
# ── 7. Watchdog ───────────────────────────────────────────────────────────────
```

new_string:
```
# ── 6c. Ollama ─────────────────────────────────────────────────────────────────

info "Checking Ollama..."

if ! command -v ollama &>/dev/null; then
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh || \
        warn "Ollama install failed — analysis/ml/watchdog will fall back to Claude/Gemini. Install manually later: https://ollama.com/download"
else
    info "Ollama already installed — skipping."
fi

if command -v ollama &>/dev/null; then
    OLLAMA_OVERRIDE_DIR="/etc/systemd/system/ollama.service.d"
    OLLAMA_OVERRIDE_FILE="$OLLAMA_OVERRIDE_DIR/override.conf"

    if [[ -f "$OLLAMA_OVERRIDE_FILE" ]]; then
        info "Ollama systemd override already exists — leaving as-is (may contain host-specific customizations)."
    else
        info "Configuring Ollama to listen on all interfaces (required for Docker containers to reach it)..."
        mkdir -p "$OLLAMA_OVERRIDE_DIR"
        cat > "$OLLAMA_OVERRIDE_FILE" <<'OLLAMA_EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
OLLAMA_EOF
        systemctl daemon-reload
        systemctl enable --now ollama || warn "Could not start Ollama service — check: systemctl status ollama"
        systemctl restart ollama || warn "Could not restart Ollama with new config — check: systemctl status ollama"
    fi
fi

# ── 7. Watchdog ───────────────────────────────────────────────────────────────
```

- [ ] **Step 3: Syntax-check the script**

Run: `bash -n /opt/alphadivision/deploy.sh`
Expected: no output, exit code 0

- [ ] **Step 4: Verify the new step's logic against the live host**

Run:
```bash
command -v ollama && echo "SKIP: Ollama already installed"
```
Expected: prints the ollama binary path, then `SKIP: Ollama already installed` — confirms the idempotent skip-install path. (The fresh-install branch itself can't be exercised on this host since Ollama is already installed; the command is the single well-known official installer documented at ollama.com/download, requiring no further verification.)

Run:
```bash
[[ -f /etc/systemd/system/ollama.service.d/override.conf ]] && echo "SKIP: override already exists, would leave as-is"
```
Expected: `SKIP: override already exists, would leave as-is` — confirms this host's existing override (which has additional `OLLAMA_MODELS`/`GODEBUG` lines from earlier manual fixes this session) is correctly left untouched, not clobbered.

- [ ] **Step 5: Commit**

```bash
cd /opt/alphadivision
git add deploy.sh
git commit -m "$(cat <<'EOF'
feat(deploy): install Ollama and configure it for Docker reachability

Nothing installed Ollama or configured it for containers to reach it
(OLLAMA_HOST defaults to loopback-only) — every model pull and Ollama
health check on a fresh migration would fail from scratch. Uses the
official installer and only sets OLLAMA_HOST, never OLLAMA_MODELS —
overriding the models path to somewhere outside Ollama's own default,
self-consistently-owned directory is exactly what caused this
session's model-pull ownership bug.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Add Ollama model-pull step to deploy.sh

**Files:**
- Modify: `deploy.sh` (insert new section before the `# ── 7. Watchdog` header — lands after Task 5's Ollama-install section since that task's insertion happens first and shares the same anchor)

**Interfaces:**
- Consumes: `ollama` command on `$PATH` (from Task 5), `$INSTALL_DIR/config.toml`.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Read the current file and locate the anchor**

Run: `sed -n '/# ── 7\. Watchdog/p' /opt/alphadivision/deploy.sh`
Expected: `# ── 7. Watchdog ───────────────────────────────────────────────────────────────` (still present and unique)

- [ ] **Step 2: Insert the new section**

Use the Edit tool on `/opt/alphadivision/deploy.sh` with:

old_string:
```
# ── 7. Watchdog ───────────────────────────────────────────────────────────────
```

new_string:
```
# ── 6d. Ollama models ──────────────────────────────────────────────────────────

if command -v ollama &>/dev/null; then
    info "Pulling configured Ollama models..."

    pip3 install --quiet tomli 2>/dev/null || true

    MODELS=$(python3 -c "
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
with open('$INSTALL_DIR/config.toml', 'rb') as f:
    cfg = tomllib.load(f)
models = set()
for section, key in (('analysis', 'ollama_model'), ('ml', 'ollama_codegen_model'), ('watchdog', 'ollama_model')):
    val = cfg.get(section, {}).get(key)
    if val:
        models.add(val)
print('\n'.join(sorted(models)))
" 2>/dev/null || true)

    if [[ -z "$MODELS" ]]; then
        warn "Could not determine Ollama models from config.toml — skipping model pulls."
    else
        while IFS= read -r MODEL; do
            [[ -z "$MODEL" ]] && continue
            info "Pulling $MODEL..."
            ollama pull "$MODEL" || warn "Failed to pull $MODEL — the corresponding feature will fail until retried manually: ollama pull $MODEL"
        done <<< "$MODELS"
    fi
else
    warn "Ollama not available — skipping model pulls."
fi

# ── 7. Watchdog ───────────────────────────────────────────────────────────────
```

- [ ] **Step 3: Syntax-check the script**

Run: `bash -n /opt/alphadivision/deploy.sh`
Expected: no output, exit code 0

- [ ] **Step 4: Verify the config.toml parsing logic against the live host**

Run:
```bash
pip3 install --quiet tomli 2>/dev/null || true
python3 -c "
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
with open('/opt/alphadivision/config.toml', 'rb') as f:
    cfg = tomllib.load(f)
models = set()
for section, key in (('analysis', 'ollama_model'), ('ml', 'ollama_codegen_model'), ('watchdog', 'ollama_model')):
    val = cfg.get(section, {}).get(key)
    if val:
        models.add(val)
print('\n'.join(sorted(models)))
"
```
Expected exact output (matching `config.toml`'s current `[analysis]`, `[ml]`, `[watchdog]` sections):
```
deepseek-r1:7b
qwen2.5-coder:7b
qwen2.5:7b
```

- [ ] **Step 5: Verify `ollama pull` is safe/idempotent for an already-present model**

Run: `ollama pull qwen2.5:7b`
Expected: completes quickly and prints `success` (Ollama checks the local digest against the registry manifest and no-ops the download since it's already present) — confirms this step is safe to actually execute for real on the live host without wasting bandwidth or risking disruption.

- [ ] **Step 6: Commit**

```bash
cd /opt/alphadivision
git add deploy.sh
git commit -m "$(cat <<'EOF'
feat(deploy): pull Ollama models from config.toml

Reads ollama_model / ollama_codegen_model / [watchdog].ollama_model
out of config.toml rather than hardcoding the model list, so this
step stays correct automatically if the configured models ever
change. Every model this system currently depends on (qwen2.5:7b,
qwen2.5-coder:7b, deepseek-r1:7b) had to be pulled by hand this
session — deploy.sh now does it unattended.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Update the deploy script design spec

**Files:**
- Modify: `docs/superpowers/specs/2026-05-16-deploy-script-design.md`

**Interfaces:**
- Consumes: the final state of `deploy.sh` after Tasks 1–6.
- Produces: nothing other tasks depend on — this is documentation only.

- [ ] **Step 1: Read the final deploy.sh in full**

Run: `cat -n /opt/alphadivision/deploy.sh`

Confirm it now contains, in order: Preflight, System dependencies, Docker group membership, Repo setup, Repo ownership, `.env` check, Backup directory, Docker services, Database migrations, Ollama, Ollama models, Watchdog (pointing at `services/watchdog/install.sh`), Backup cron, Health check.

- [ ] **Step 2: Update the spec's Script Sections list**

Use the Edit tool on `/opt/alphadivision/docs/superpowers/specs/2026-05-16-deploy-script-design.md` with:

old_string:
```
### 7. Watchdog
- Run `sudo bash /opt/alphadivision/watchdog/install.sh`
- Already idempotent: restarts the service if already running, installs fresh if not
- Installs Python deps for watchdog as a side effect
```

new_string:
```
### 2b. Docker Group Membership
- Adds the deploying user (`$SUDO_USER`, falling back to `ubuntu`) to the `docker` group
- Required for the watchdog (see `services/watchdog/`) to read `docker compose logs` — without this it silently sees zero errors, ever
- Idempotent — skipped if already a member

### 3b. Repo Ownership
- `chown -R "$DEPLOY_USER:$DEPLOY_USER" "$INSTALL_DIR"` after the clone/pull step
- The script runs entirely as root, so without this every file it touches (`.git`, build state) ends up root-owned while the watchdog and interactive operator work both run as the deploy user
- Hard error on failure — nothing downstream should be trusted if this fails

### 6b. Database Migrations
- Waits for Postgres healthy (`pg_isready`, up to 60s), then applies every file in `db/migrations/*.sql` in sorted order via `docker compose exec -T postgres psql -v ON_ERROR_STOP=1`
- Only `db/schema.sql` auto-runs on first Postgres init (via `docker-entrypoint-initdb.d`) — the numbered migrations never did, until now
- All existing migrations are `IF NOT EXISTS`-guarded, so safe to re-run every deploy; a genuine SQL failure is a hard error

### 6c. Ollama
- Installs Ollama via the official installer (`curl -fsSL https://ollama.com/install.sh | sh`) if not already present — warns, doesn't fail, if the installer fails (Claude/Gemini remain usable without it)
- Sets `OLLAMA_HOST=0.0.0.0:11434` via a systemd override **only if no override file already exists** — never overrides `OLLAMA_MODELS`, since pointing it at a path outside Ollama's own default (self-consistently-owned) directory is what caused a real ownership bug in production

### 6d. Ollama Models
- Parses `ollama_model` / `ollama_codegen_model` / `[watchdog].ollama_model` out of `config.toml` (via Python's `tomllib`/`tomli`) and `ollama pull`s each, deduped
- Read dynamically rather than hardcoded so this step can't go stale if the configured models change
- Per-model warning (not a hard failure) if a pull fails

### 7. Watchdog
- Run `sudo bash /opt/alphadivision/services/watchdog/install.sh` (the real LLM-based log classifier — the legacy Redis-heartbeat watchdog at `watchdog/` was deleted; it was a strict subset of this and running both risked double-remediation)
- Already idempotent: restarts the service if already running, installs fresh if not
- Installs Python deps for watchdog as a side effect
```

- [ ] **Step 3: Update the spec's Idempotency Summary table**

Use the Edit tool on the same file with:

old_string:
```
| Watchdog | `install.sh` restarts if already running |
| Cron | Grep-checks before appending |
```

new_string:
```
| Docker group | Skipped if user already a member |
| Repo chown | `chown -R` is a no-op if already correctly owned |
| DB migrations | Every migration is `IF NOT EXISTS`-guarded |
| Ollama install | Skipped if `ollama` already on `$PATH` |
| Ollama override | Skipped if the override file already exists |
| Ollama models | `ollama pull` no-ops if the model is already present |
| Watchdog | `install.sh` restarts if already running |
| Cron | Grep-checks before appending |
```

- [ ] **Step 4: Update the spec's Out of Scope section**

Use the Edit tool on the same file with:

old_string:
```
## Out of Scope

- SSL/TLS setup (handled separately via Tailscale)
- Firewall configuration
- `.env` generation or secret rotation
- Multi-VM or containerised deployment
```

new_string:
```
## Out of Scope

- SSL/TLS setup (handled separately via Tailscale)
- Firewall configuration
- `.env` generation or secret rotation
- Multi-VM or containerised deployment

See also: `docs/superpowers/specs/2026-07-13-deploy-script-migration-readiness-design.md`
for the design rationale behind the watchdog swap, Ollama install, DB migrations,
and repo-ownership steps added on 2026-07-13.
```

- [ ] **Step 5: Verify the doc reads coherently end to end**

Run: `cat /opt/alphadivision/docs/superpowers/specs/2026-05-16-deploy-script-design.md`

Confirm: no references remain to `watchdog/install.sh` (only `services/watchdog/install.sh`), the step numbering reads sensibly top to bottom, and the new sections don't contradict the (unchanged) Error Handling / File Location sections above them.

- [ ] **Step 6: Commit**

```bash
cd /opt/alphadivision
git add -f docs/superpowers/specs/2026-05-16-deploy-script-design.md
git commit -m "$(cat <<'EOF'
docs: update deploy script spec for migration-readiness fixes

Reflects the docker-group, repo-chown, DB-migration, Ollama, and
watchdog-swap steps added to deploy.sh.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Post-Implementation Verification

After all 7 tasks are complete, run this end-to-end sanity check (does not restart any running service, purely read-only):

```bash
bash -n /opt/alphadivision/deploy.sh && echo "deploy.sh: syntax OK"
bash -n /opt/alphadivision/services/watchdog/install.sh && echo "install.sh: syntax OK"
bash -n /opt/alphadivision/services/watchdog/uninstall.sh && echo "uninstall.sh: syntax OK"
[[ ! -d /opt/alphadivision/watchdog ]] && echo "legacy watchdog/ removed: OK"
grep -c "^# ── " /opt/alphadivision/deploy.sh
sudo systemctl is-active alphadivision-watchdog
```

Expected: three "syntax OK" lines, "legacy watchdog/ removed: OK", a section count of 17 (12 baseline headers — including `Config`/`Colours` — plus the 5 new: `2b`, `3b`, `6b`, `6c`, `6d`), and `active`.
