import json
import pytest
from unittest.mock import patch, MagicMock

from log_monitor import collect_errors


SAMPLE_LOGS = """\
analysis-1  | {"timestamp": "2026-07-13T17:00:52Z", "service": "analysis", "level": "INFO", "message": "Stage 1 passed"}
analysis-1  | {"timestamp": "2026-07-13T17:00:53Z", "service": "analysis", "level": "ERROR", "message": "AI call failed: Connection refused"}
dashboard-1  | {"timestamp": "2026-07-13T17:00:54Z", "service": "dashboard", "level": "ERROR", "message": "IndexError: tuple index out of range"}
data-1  | {"timestamp": "2026-07-13T17:00:55Z", "service": "data", "level": "INFO", "message": "Heartbeat ok"}
ml-1  | non-json log line with ERROR keyword
"""


def _mock_run(stdout):
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = stdout
    return mock


def test_collect_errors_returns_only_error_lines():
    with patch("log_monitor.subprocess.run", return_value=_mock_run(SAMPLE_LOGS)):
        errors = collect_errors("/fake/docker-compose.yml", since_seconds=35)
    assert len(errors) == 3
    services = [e["service"] for e in errors]
    assert "analysis" in services
    assert "dashboard" in services
    assert "ml" in services


def test_collect_errors_parses_service_name():
    with patch("log_monitor.subprocess.run", return_value=_mock_run(SAMPLE_LOGS)):
        errors = collect_errors("/fake/docker-compose.yml")
    analysis_err = next(e for e in errors if e["service"] == "analysis")
    assert "Connection refused" in analysis_err["message"]


def test_collect_errors_handles_non_json_lines():
    with patch("log_monitor.subprocess.run", return_value=_mock_run(SAMPLE_LOGS)):
        errors = collect_errors("/fake/docker-compose.yml")
    ml_err = next(e for e in errors if e["service"] == "ml")
    assert "ERROR" in ml_err["message"]


def test_collect_errors_returns_empty_on_no_errors():
    no_errors = "analysis-1  | {\"level\": \"INFO\", \"message\": \"ok\"}\n"
    with patch("log_monitor.subprocess.run", return_value=_mock_run(no_errors)):
        errors = collect_errors("/fake/docker-compose.yml")
    assert errors == []


def test_collect_errors_handles_subprocess_failure():
    mock = MagicMock()
    mock.returncode = 1
    mock.stdout = ""
    with patch("log_monitor.subprocess.run", return_value=mock):
        errors = collect_errors("/fake/docker-compose.yml")
    assert errors == []
