# AlphaDivision Database Backup

Daily PostgreSQL backup script. Dumps the database from inside Docker, compresses it,
uploads to Oracle Cloud Object Storage, and enforces 30-day retention.

Runs **outside Docker** on the VM host. No shared modules required.

---

## How It Works

1. `docker compose exec -T postgres pg_dump` streams the database out of the running container
2. Output is gzip-compressed and saved to `BACKUP_DIR/alphadivision-YYYYMMDD.sql.gz`
3. File is uploaded to OCI Object Storage via the `oci` CLI
4. Local and OCI copies older than 30 days are deleted

---

## Prerequisites

### 1. Python dependency

```bash
pip3 install python-dotenv==1.0.1
```

### 2. OCI CLI

Install and configure the OCI CLI on the VM host:

```bash
bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"
oci setup config
```

Follow the prompts — you'll need your OCI tenancy OCID, user OCID, and an API key.

### 3. Create an OCI bucket

In the OCI Console: Object Storage → Buckets → Create Bucket. Name it `alphadivision-backups` (or your choice).

### 4. Create the local backup directory

```bash
sudo mkdir -p /backups/alphadivision
sudo chown ubuntu:ubuntu /backups/alphadivision
```

---

## Environment Variables

Add to `/opt/alphadivision/.env`:

```ini
POSTGRES_USER=your_postgres_user

# OCI Object Storage
OCI_BUCKET_NAME=alphadivision-backups
OCI_NAMESPACE=your-oci-namespace   # found in OCI Console → Tenancy Details → Object Storage Namespace

# Optional overrides
BACKUP_DIR=/backups/alphadivision
COMPOSE_DIR=/opt/alphadivision
```

Find your OCI namespace: `oci os ns get`

---

## Running Manually

```bash
python3 /opt/alphadivision/backup/backup.py
echo $?   # 0 = success, 1 = failure
```

---

## Cron Setup (Recommended)

Run twice daily at midnight and noon:

```bash
crontab -e
```

Add:

```cron
0 0,12 * * * /usr/bin/python3 /opt/alphadivision/backup/backup.py >> /var/log/alphadivision-backup.log 2>&1
```

Unlike the watchdog, the backup script runs a **single cycle and exits**, making it safe for cron.

---

## Restoring from Backup

### From local file

```bash
gunzip < /backups/alphadivision/alphadivision-20260515.sql.gz | \
  docker compose exec -T postgres psql -U $POSTGRES_USER alphadivision
```

### From OCI

```bash
# Download
oci os object get \
  --bucket-name alphadivision-backups \
  --namespace your-namespace \
  --name alphadivision-20260515.sql.gz \
  --file /tmp/restore.sql.gz

# Restore
gunzip < /tmp/restore.sql.gz | \
  docker compose exec -T postgres psql -U $POSTGRES_USER alphadivision
```

---

## Running Tests

```bash
cd /opt/alphadivision
python3 -m pytest backup/tests/ -v
```

---

## Failure Modes

| Scenario | Behaviour |
|---|---|
| `pg_dump` fails (DB unreachable) | Logged as ERROR; upload and pruning skipped; exits with code 1 |
| OCI upload fails | Logged as ERROR; local file retained; pruning still runs; exits with code 1 |
| OCI list/delete fails during pruning | Logged as ERROR; remaining objects still processed |
| Missing env var at startup | CRITICAL log; exits with code 1 |
| Local backup dir missing | Created automatically via `mkdir -p` |
| Cron doesn't run (VM offline) | No gap-filling; most recent backup used for restore; max 24h data loss per spec |
