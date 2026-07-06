# Deploy Script Design

**Date:** 2026-05-16
**Status:** Approved

---

## Goal

A single idempotent bash script (`deploy.sh`) at the repo root that handles both first-time VM setup and subsequent code updates. Safe to re-run at any time.

---

## Target Environment

- Oracle Cloud VM running Ubuntu
- Repo deployed at `/opt/alphadivision`
- Must run as root (`sudo bash deploy.sh`)

---

## Required Input

One environment variable before running:

```bash
REPO_URL=https://github.com/youruser/alphadivision.git sudo bash deploy.sh
```

Hardcoded fallback allowed once the repo URL is known and stable.

---

## Script Sections

### 1. Preflight
- Assert running as root; abort with clear message if not
- Confirm Ubuntu (warn but continue on other distros)

### 2. System Dependencies
- If `docker` not found: install Docker CE + compose plugin via official apt repo (`https://download.docker.com/linux/ubuntu`)
- If `python3` not found: `apt-get install -y python3 python3-pip`
- If `pip3` not found: install via apt
- Each check is independent — only installs what's missing

### 3. Repo Setup
- If `/opt/alphadivision` does not exist: `git clone $REPO_URL /opt/alphadivision`
- If it exists: `git pull` from inside the directory
- `git pull` is safe while Docker services are running — containers are isolated from host source files (except mounted volumes, which are config/library files only)

### 4. `.env` Check
- If `/opt/alphadivision/.env` is missing: print clear error with list of required keys, then `exit 1`
- Never create or modify `.env` — secrets are always manual

Required `.env` keys (matches `.env.example`):
```
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
```

Backup-specific vars (also in `.env`, read by `backup.py`):
```
OCI_BUCKET_NAME
OCI_NAMESPACE
```

### 5. Backup Directory
- `mkdir -p /backups/alphadivision && chown ubuntu:ubuntu /backups/alphadivision`
- Idempotent — no-op if already exists

### 6. Docker Services
- `docker compose build` — rebuild all images
- `docker compose up -d` — rolling restart; only restarts services whose image changed
- Both commands run from `/opt/alphadivision`

### 7. Watchdog
- Run `sudo bash /opt/alphadivision/watchdog/install.sh`
- Already idempotent: restarts the service if already running, installs fresh if not
- Installs Python deps for watchdog as a side effect

### 8. Backup Cron
- Install pip dep: `pip3 install --quiet python-dotenv==1.0.1`
- Check if cron entry already exists: `crontab -l 2>/dev/null | grep -q backup.py`
- If not found: append `0 0,12 * * * /usr/bin/python3 /opt/alphadivision/backup/backup.py >> /var/log/alphadivision-backup.log 2>&1`
- Idempotent — only adds if absent

### 9. Health Check
- Poll `http://localhost:8080/health` every 2 seconds, up to 30 seconds
- Print `[OK] Dashboard healthy` on 200 response
- Print `[WARN] Dashboard did not respond within 30s — check logs` if timeout
- Non-blocking: a failed health check prints a warning but does not exit non-zero (services may still be starting)

---

## Idempotency Summary

| Step | How it's idempotent |
|---|---|
| Docker install | Skipped if `docker` already in PATH |
| Python install | Skipped if `python3` already in PATH |
| git clone | Skipped if `/opt/alphadivision` exists; runs `git pull` instead |
| `.env` check | Abort only — never modifies |
| Backup dir | `mkdir -p` is a no-op if exists |
| Docker up | `up -d` only restarts services with changed images |
| Watchdog | `install.sh` restarts if already running |
| Cron | Grep-checks before appending |

---

## Error Handling

- Any command failure aborts the script (`set -euo pipefail`)
- Health check is the only non-fatal step — warns and continues
- All output is prefixed with `[INFO]`, `[WARN]`, or `[ERROR]` for easy log scanning

---

## File Location

```
/opt/alphadivision/deploy.sh
```

No new directories. No new services. One file.

---

## Out of Scope

- SSL/TLS setup (handled separately via Tailscale)
- Firewall configuration
- `.env` generation or secret rotation
- Multi-VM or containerised deployment
