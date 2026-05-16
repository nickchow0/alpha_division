"""
backup.py — AlphaDivision PostgreSQL backup script.

Runs on the VM host (outside Docker). Dumps the database via docker compose exec,
uploads to Oracle Cloud Object Storage, and prunes backups older than 30 days.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_NAME = "alphadivision"
BACKUP_FILENAME_PREFIX = "alphadivision-"
RETENTION_DAYS = 30

# Path to docker-compose project directory (same as watchdog)
COMPOSE_DIR = os.environ.get("COMPOSE_DIR", "/opt/alphadivision")
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups/alphadivision"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("backup")

# ---------------------------------------------------------------------------
# Database dump
# ---------------------------------------------------------------------------

def run_pg_dump(pg_user: str, db_name: str, output_path: str) -> bool:
    """
    Dump the PostgreSQL database inside the running Docker container and
    write the result as a gzip-compressed file to output_path.
    Returns True on success, False on failure.
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres",
             "pg_dump", "-U", pg_user, db_name],
            cwd=COMPOSE_DIR,
            capture_output=True,
            timeout=300,
        )
        if result.returncode != 0:
            log.error("pg_dump failed (exit %d): %s", result.returncode,
                      result.stderr.decode(errors="replace"))
            return False
        if not result.stdout:
            log.error("pg_dump returned empty output — aborting backup")
            return False
        with gzip.open(output_path, "wb") as f:
            f.write(result.stdout)
        size_kb = Path(output_path).stat().st_size // 1024
        log.info("Dump written to %s (%d KB)", output_path, size_kb)
        return True
    except Exception as exc:
        log.error("Exception during pg_dump: %s", exc)
        return False


# ---------------------------------------------------------------------------
# OCI Object Storage
# ---------------------------------------------------------------------------

def upload_to_oci(bucket: str, namespace: str, object_name: str, file_path: str) -> bool:
    """
    Upload a file to Oracle Cloud Object Storage via the oci CLI.
    Returns True on success, False on failure.
    """
    try:
        result = subprocess.run(
            [
                "oci", "os", "object", "put",
                "--bucket-name", bucket,
                "--namespace", namespace,
                "--name", object_name,
                "--file", file_path,
                "--force",
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            log.error("OCI upload failed (exit %d): %s", result.returncode,
                      result.stderr.decode(errors="replace"))
            return False
        log.info("Uploaded %s to OCI bucket %s", object_name, bucket)
        return True
    except Exception as exc:
        log.error("Exception during OCI upload: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

def prune_local_backups(
    backup_dir: str,
    retention_days: int = RETENTION_DAYS,
    today: "date | None" = None,
) -> list:
    """
    Delete local .sql.gz backup files older than retention_days.
    Filenames must match `alphadivision-YYYYMMDD.sql.gz`.
    Returns list of deleted filenames (not full paths).
    """
    if today is None:
        today = date.today()
    cutoff = today - timedelta(days=retention_days)
    deleted = []
    backup_path = Path(backup_dir)
    if not backup_path.exists():
        return []
    for f in backup_path.glob(f"{BACKUP_FILENAME_PREFIX}*.sql.gz"):
        stem = f.stem  # e.g. "alphadivision-20260516.sql" (.gz stripped by pathlib)
        date_part = stem.removeprefix(BACKUP_FILENAME_PREFIX).removesuffix(".sql")
        try:
            file_date = datetime.strptime(date_part, "%Y%m%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            log.info("Deleting local backup: %s", f.name)
            f.unlink()
            deleted.append(f.name)
    return deleted


def prune_oci_backups(
    bucket: str,
    namespace: str,
    retention_days: int = RETENTION_DAYS,
    today: "date | None" = None,
) -> list:
    """
    Delete OCI Object Storage backups older than retention_days.
    Returns list of deleted object names.
    """
    if today is None:
        today = date.today()
    cutoff = today - timedelta(days=retention_days)
    deleted = []

    # List all objects in the bucket
    try:
        list_result = subprocess.run(
            [
                "oci", "os", "object", "list",
                "--bucket-name", bucket,
                "--namespace", namespace,
                "--all",
            ],
            capture_output=True,
            timeout=60,
        )
        if list_result.returncode != 0:
            log.error("OCI list failed: %s", list_result.stderr.decode(errors="replace"))
            return []
        data = json.loads(list_result.stdout).get("data", [])
    except Exception as exc:
        log.error("Exception listing OCI objects: %s", exc)
        return []

    for obj in data:
        name = obj.get("name", "")
        if not name.startswith(BACKUP_FILENAME_PREFIX):
            continue
        date_part = name.removeprefix(BACKUP_FILENAME_PREFIX).removesuffix(".sql.gz")
        try:
            file_date = datetime.strptime(date_part, "%Y%m%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                del_result = subprocess.run(
                    [
                        "oci", "os", "object", "delete",
                        "--bucket-name", bucket,
                        "--namespace", namespace,
                        "--name", name,
                        "--force",
                    ],
                    capture_output=True,
                    timeout=60,
                )
                if del_result.returncode == 0:
                    log.info("Deleted OCI object: %s", name)
                    deleted.append(name)
                else:
                    log.error("Failed to delete OCI object %s: %s", name,
                              del_result.stderr.decode(errors="replace"))
            except Exception as exc:
                log.error("Exception deleting OCI object %s: %s", name, exc)

    return deleted


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_backup(cfg: dict, today: "date | None" = None) -> bool:
    """
    Run a full backup cycle:
      1. pg_dump → compressed local file
      2. Upload to OCI Object Storage
      3. Prune local backups older than RETENTION_DAYS
      4. Prune OCI backups older than RETENTION_DAYS

    Returns True only if both dump and upload succeeded.
    Pruning always runs regardless of upload success.
    """
    if today is None:
        today = date.today()

    filename = f"{BACKUP_FILENAME_PREFIX}{today.strftime('%Y%m%d')}.sql.gz"
    backup_dir = Path(cfg["backup_dir"])
    backup_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(backup_dir / filename)

    log.info("Starting backup: %s", filename)

    # Step 1: Dump
    dump_ok = run_pg_dump(cfg["pg_user"], cfg["db_name"], output_path)
    if not dump_ok:
        log.error("Backup failed at pg_dump step")
        return False

    # Step 2: Upload
    upload_ok = upload_to_oci(cfg["oci_bucket"], cfg["oci_namespace"], filename, output_path)
    if not upload_ok:
        log.error("Backup upload failed — local file retained at %s", output_path)

    # Step 3 & 4: Prune (always runs, even if upload failed)
    prune_local_backups(cfg["backup_dir"], today=today)
    prune_oci_backups(cfg["oci_bucket"], cfg["oci_namespace"], today=today)

    if dump_ok and upload_ok:
        log.info("Backup complete: %s", filename)
    return dump_ok and upload_ok


def main() -> None:
    """Entry point: load config from environment and run one backup cycle."""
    env_path = "/opt/alphadivision/.env"
    if not os.path.exists(env_path):
        env_path = ".env"
    load_dotenv(env_path)

    required_vars = ["POSTGRES_USER", "OCI_BUCKET_NAME", "OCI_NAMESPACE"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        log.critical("Missing required environment variables: %s", ", ".join(missing))
        raise SystemExit(1)

    cfg = {
        "pg_user": os.environ["POSTGRES_USER"],
        "db_name": DB_NAME,
        "backup_dir": str(BACKUP_DIR),
        "oci_bucket": os.environ["OCI_BUCKET_NAME"],
        "oci_namespace": os.environ["OCI_NAMESPACE"],
    }

    success = run_backup(cfg)
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
