#!/usr/bin/env bash
# deploy.sh — AlphaDivision deployment script.
#
# Handles first-time VM setup and subsequent code updates.
# Safe to re-run at any time (idempotent).
#
# Usage (on the Oracle VM):
#   sudo bash /opt/alphadivision/deploy.sh
#
# On first run (before repo exists on VM):
#   REPO_URL=https://github.com/nickchow0/alphadivision.git sudo bash deploy.sh
#
# Requirements:
#   - Ubuntu (other distros: apt commands may fail)
#   - Internet access
#   - /opt/alphadivision/.env must be created manually before running

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────

REPO_URL="${REPO_URL:-https://github.com/nickchow0/alphadivision.git}"
INSTALL_DIR="/opt/alphadivision"
BACKUP_DIR="/backups/alphadivision"
BACKUP_LOG="/var/log/alphadivision-backup.log"

# ── Colours ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── 1. Preflight ──────────────────────────────────────────────────────────────

info "Starting AlphaDivision deployment..."

[[ $EUID -eq 0 ]] || error "Run as root: sudo bash $0"

if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    if [[ "${ID:-}" != "ubuntu" ]]; then
        warn "Non-Ubuntu distro detected (${ID:-unknown}). apt commands may fail."
    fi
fi

# ── 2. System dependencies ────────────────────────────────────────────────────

info "Checking system dependencies..."

if ! command -v docker &>/dev/null; then
    info "Installing Docker CE..."
    apt-get update -qq
    apt-get install -y ca-certificates curl gnupg lsb-release
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    info "Docker installed."
else
    info "Docker already installed — skipping."
fi

if ! command -v python3 &>/dev/null; then
    info "Installing Python 3..."
    apt-get install -y python3
    info "Python 3 installed."
else
    info "Python 3 already installed — skipping."
fi

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
        if usermod -aG docker "$DEPLOY_USER"; then
            info "$DEPLOY_USER added to docker group (takes effect for new processes, e.g. the watchdog systemd service started later in this script)."
        else
            warn "Failed to add $DEPLOY_USER to docker group — add manually: sudo usermod -aG docker $DEPLOY_USER"
        fi
    fi
else
    warn "User '$DEPLOY_USER' not found — skipping docker group setup. Add it manually: sudo usermod -aG docker <user>"
fi

# ── 3. Repo setup ─────────────────────────────────────────────────────────────

info "Setting up repository..."

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    info "Cloning repository to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    info "Repository cloned."
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

info "Checking .env..."

ENV_FILE="$INSTALL_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    error "$(cat <<MSG
.env not found at $ENV_FILE

Create it with the following keys (see .env.example for format):

  DATABASE_URL
  POSTGRES_USER
  POSTGRES_PASSWORD
  REDIS_URL
  ALPACA_API_KEY
  ALPACA_SECRET_KEY
  ALPACA_BASE_URL
  ANTHROPIC_API_KEY
  FINNHUB_API_KEY
  FRED_API_KEY
  DISCORD_WEBHOOK_URL
  SENDGRID_API_KEY
  ALERT_EMAIL_TO
  ALERT_EMAIL_FROM
  OCI_BUCKET_NAME   (backup only)
  OCI_NAMESPACE     (backup only)

Then re-run: sudo bash $0
MSG
)"
fi

info ".env found."

# ── 5. Backup directory ───────────────────────────────────────────────────────

info "Creating backup directory..."
mkdir -p "$BACKUP_DIR"
chown ubuntu:ubuntu "$BACKUP_DIR"
info "Backup directory ready: $BACKUP_DIR"

# ── 6. Docker services ────────────────────────────────────────────────────────

info "Building Docker images..."
docker compose -f "$INSTALL_DIR/docker-compose.yml" build

info "Starting Docker services..."
docker compose -f "$INSTALL_DIR/docker-compose.yml" up -d

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

info "Installing watchdog service..."
bash "$INSTALL_DIR/services/watchdog/install.sh"
info "Watchdog installed."

# ── 8. Backup cron ────────────────────────────────────────────────────────────

info "Configuring backup cron job..."
pip3 install --quiet python-dotenv==1.0.1

CRON_ENTRY="0 0,12 * * * /usr/bin/python3 $INSTALL_DIR/backup/backup.py >> $BACKUP_LOG 2>&1"

if crontab -l 2>/dev/null | grep -qF "backup.py"; then
    info "Backup cron job already configured — skipping."
else
    (crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -
    info "Backup cron job added (runs at midnight and noon)."
fi

# ── 9. Health check ───────────────────────────────────────────────────────────

info "Waiting for dashboard to become healthy..."

HEALTH_URL="http://localhost:8080/health"
TIMEOUT=30
ELAPSED=0

while [[ $ELAPSED -lt $TIMEOUT ]]; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || true)
    if [[ "$HTTP_CODE" == "200" ]]; then
        info "[OK] Dashboard healthy at $HEALTH_URL"
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

if [[ $ELAPSED -ge $TIMEOUT ]]; then
    warn "Dashboard did not respond within ${TIMEOUT}s — check logs:"
    warn "  docker compose -f $INSTALL_DIR/docker-compose.yml logs --tail=50"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
info "AlphaDivision deployment complete."
info "Dashboard: http://localhost:8080"
info "Watchdog:  sudo systemctl status alphadivision-watchdog"
info "Logs:      docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
