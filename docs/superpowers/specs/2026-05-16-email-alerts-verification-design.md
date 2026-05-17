# Email Alerts Verification Design

**Date:** 2026-05-16
**Status:** Approved

---

## Goal

A one-time script that verifies the SendGrid email and Discord webhook integrations work end-to-end by exercising the exact same code path the alerts service uses in production.

---

## File

```
scripts/test_email.py
```

No new directories needed — `scripts/` sits at the repo root alongside `backup/` and `watchdog/`.

---

## What It Does

1. Loads `.env` from `/opt/alphadivision/.env` (falls back to `.env` in current directory)
2. Checks all required env vars are present; aborts with a clear message listing any missing ones
3. Calls `send_email()` from `services/alerts/notifier.py` with a test subject and body
4. Calls `send_discord()` from `services/alerts/notifier.py` with a test message
5. Prints `[OK]` or `[FAIL]` for each, with the exception message on failure
6. Exits 0 if both passed, 1 if either failed

---

## Required Env Vars

```
SENDGRID_API_KEY
ALERT_EMAIL_FROM
ALERT_EMAIL_TO
DISCORD_WEBHOOK_URL
```

---

## Usage

```bash
python3 /opt/alphadivision/scripts/test_email.py
```

Expected output on success:
```
[OK] Email sent to you@example.com
[OK] Discord webhook fired
```

---

## Constraints

- No new dependencies — reuses `sendgrid` and `requests` already installed by the alerts service
- No changes to the alerts service or notifier
- Disposable — not part of the automated test suite
- Self-contained — adds `services/alerts/` to `sys.path` to import `notifier.py` directly
