import json
import time
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import tempfile
import os

from deduplicator import (
    fingerprint, is_suppressed, record_seen,
    record_restart, restart_count_in_window,
)


def _tmp_state():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"error_cooldowns": {}, "restart_counts": {}}, f)
    f.close()
    return f.name


def test_fingerprint_is_stable():
    fp1 = fingerprint("analysis", "Connection refused on port 11434")
    fp2 = fingerprint("analysis", "Connection refused on port 11434")
    assert fp1 == fp2


def test_fingerprint_differs_by_service():
    fp1 = fingerprint("analysis", "same error")
    fp2 = fingerprint("dashboard", "same error")
    assert fp1 != fp2


def test_fingerprint_length():
    fp = fingerprint("analysis", "some error")
    assert len(fp) == 16


def test_not_suppressed_when_fresh(tmp_path):
    state_file = str(tmp_path / "state.json")
    fp = fingerprint("analysis", "some error")
    assert not is_suppressed(fp, state_file, cooldown_minutes=10)


def test_suppressed_immediately_after_record(tmp_path):
    state_file = str(tmp_path / "state.json")
    fp = fingerprint("analysis", "some error")
    record_seen(fp, state_file)
    assert is_suppressed(fp, state_file, cooldown_minutes=10)


def test_not_suppressed_after_cooldown_expires(tmp_path):
    state_file = str(tmp_path / "state.json")
    fp = fingerprint("analysis", "some error")
    # Write a timestamp 11 minutes ago
    past = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    state = {"error_cooldowns": {fp: past}, "restart_counts": {}}
    with open(state_file, "w") as f:
        json.dump(state, f)
    assert not is_suppressed(fp, state_file, cooldown_minutes=10)


def test_restart_count_in_window(tmp_path):
    state_file = str(tmp_path / "state.json")
    record_restart("analysis", state_file)
    record_restart("analysis", state_file)
    assert restart_count_in_window("analysis", 30, state_file) == 2


def test_restart_count_excludes_old_restarts(tmp_path):
    state_file = str(tmp_path / "state.json")
    past = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    state = {"error_cooldowns": {}, "restart_counts": {"analysis": [past, past]}}
    with open(state_file, "w") as f:
        json.dump(state, f)
    assert restart_count_in_window("analysis", 30, state_file) == 0
