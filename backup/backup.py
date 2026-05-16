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
