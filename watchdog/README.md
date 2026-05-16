# AlphaDivision Watchdog

A standalone host-level watchdog that monitors all AlphaDivision Docker services,
auto-restarts failures, and sends Discord + email alerts.

Runs **outside Docker** directly on the VM host. No shared modules required.

---

## How It Works

- Polls Redis heartbeat keys (`heartbeat:{service}`, TTL 90 s) every 2 minutes
- If a service heartbeat TTL ≤ 0, the service is considered **down**
- Attempts `docker compose restart <service>` up to 3 times (tracked in Redis, 1-hour window)
- After 3 failed restarts, escalates to **CRITICAL** Discord + email alert and stops retrying until the window expires
- When a service recovers, sends a **recovery** Discord notification
- Polls the dashboard `/health` endpoint (HTTP); alerts Discord on non-200 or timeout

---

## Prerequisites

```bash
pip3 install redis==5.0.4 requests==2.31.0 sendgrid==6.11.0 python-dotenv==1.0.1
```

---

## Environment Variables

Create `/opt/alphadivision/.env` (or a `.env` in the working directory):

```ini
REDIS_URL=redis://localhost:6379
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
SENDGRID_API_KEY=SG.xxxx
ALERT_EMAIL_FROM=alerts@yourdomain.com
ALERT_EMAIL_TO=you@yourdomain.com

# Optional: override docker-compose project directory (default: /opt/alphadivision)
COMPOSE_DIR=/opt/alphadivision
```

---

## Running Manually

```bash
python3 /opt/alphadivision/watchdog/watchdog.py
```

---

## systemd Service (Recommended)

Create `/etc/systemd/system/alphadivision-watchdog.service`:

```ini
[Unit]
Description=AlphaDivision Watchdog
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/alphadivision
ExecStart=/usr/bin/python3 /opt/alphadivision/watchdog/watchdog.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable alphadivision-watchdog
sudo systemctl start alphadivision-watchdog
sudo systemctl status alphadivision-watchdog
```

View logs:

```bash
journalctl -u alphadivision-watchdog -f
```

---

## Cron (Alternative)

Add to crontab (`crontab -e`):

```cron
*/2 * * * * /usr/bin/python3 /opt/alphadivision/watchdog/watchdog.py >> /var/log/alphadivision-watchdog.log 2>&1
```

> **⚠️ Warning:** The watchdog runs a `while True` loop internally, so a cron entry will spawn a
> second process before the first exits — causing duplicate alerts, double restarts, and racing
> Redis state. If you must use cron, you would need to restructure the script to run a single
> cycle and exit. The systemd service is strongly preferred.

---

## Running Tests

```bash
cd /opt/alphadivision
python -m pytest watchdog/tests/ -v
```

---

## Failure Modes

| Scenario | Behaviour |
|---|---|
| Redis is down | `redis.from_url` raises at startup; watchdog logs error and exits (systemd will restart it) |
| Docker compose fails | `restart_service` returns False, logs error, increments count |
| Discord webhook fails | Logged as ERROR; watchdog continues — email still attempted |
| SendGrid fails | Logged as ERROR; watchdog continues |
| Service exception during cycle | Logged as ERROR; remaining services still checked |
| Dashboard HTTP unreachable | Discord-only alert (no restart attempted) |
