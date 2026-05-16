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
