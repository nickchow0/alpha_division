import json
import sys
import os
import time
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock

import docker as docker_module
from log_reader import fetch_logs, _parse_line, SERVICES, _fetch_watchdog_logs


def _make_log(service, level, message, ts="2026-07-13T17:00:00+00:00"):
    return json.dumps({
        "timestamp": ts, "service": service,
        "level": level, "message": message,
    })


def _mock_client(container_logs: dict):
    """container_logs: {service_name: [json_line_str, ...]}"""
    mock_client = MagicMock()

    def get_container(name):
        service = name.replace("alphadivision-", "").replace("-1", "")
        if service not in container_logs:
            raise docker_module.errors.NotFound("not found")
        mock_container = MagicMock()
        lines = container_logs[service]
        mock_container.logs.return_value = ("\n".join(lines) + "\n").encode()
        return mock_container

    mock_client.containers.get.side_effect = get_container
    return mock_client


def test_fetch_logs_returns_entries():
    logs = {"analysis": [_make_log("analysis", "INFO", "Stage 1 passed")]}
    with patch("log_reader.docker.DockerClient", return_value=_mock_client(logs)):
        result = fetch_logs(since="30m", services=["analysis"])
    assert result["showing"] == 1
    assert result["logs"][0]["message"] == "Stage 1 passed"
    assert result["logs"][0]["level"] == "INFO"


def test_fetch_logs_filters_by_level():
    logs = {
        "analysis": [
            _make_log("analysis", "INFO", "ok"),
            _make_log("analysis", "ERROR", "AI call failed"),
        ]
    }
    with patch("log_reader.docker.DockerClient", return_value=_mock_client(logs)):
        result = fetch_logs(services=["analysis"], level="ERROR")
    assert result["showing"] == 1
    assert result["logs"][0]["level"] == "ERROR"


def test_fetch_logs_filters_by_keyword():
    logs = {
        "analysis": [
            _make_log("analysis", "ERROR", "AI call failed: Connection refused"),
            _make_log("analysis", "ERROR", "Stage 1 filter failed"),
        ]
    }
    with patch("log_reader.docker.DockerClient", return_value=_mock_client(logs)):
        result = fetch_logs(services=["analysis"], q="Connection")
    assert result["showing"] == 1
    assert "Connection" in result["logs"][0]["message"]


def test_fetch_logs_sorted_newest_first():
    logs = {
        "analysis": [
            _make_log("analysis", "INFO", "first",  ts="2026-07-13T17:00:00+00:00"),
            _make_log("analysis", "INFO", "second", ts="2026-07-13T17:00:01+00:00"),
        ]
    }
    with patch("log_reader.docker.DockerClient", return_value=_mock_client(logs)):
        result = fetch_logs(services=["analysis"])
    assert result["logs"][0]["message"] == "second"
    assert result["logs"][1]["message"] == "first"


def test_fetch_logs_truncates_at_limit():
    entries = [_make_log("analysis", "INFO", f"msg {i}") for i in range(10)]
    with patch("log_reader.docker.DockerClient", return_value=_mock_client({"analysis": entries})):
        result = fetch_logs(services=["analysis"], limit=5)
    assert result["showing"] == 5
    assert result["total_fetched"] == 10
    assert result["truncated"] is True


def test_fetch_logs_missing_container_skips():
    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker_module.errors.NotFound("nope")
    with patch("log_reader.docker.DockerClient", return_value=mock_client):
        result = fetch_logs(services=["analysis"])
    assert result["showing"] == 0
    assert "error" not in result


def test_fetch_logs_docker_unavailable_returns_error():
    with patch("log_reader.docker.DockerClient", side_effect=Exception("socket not found")):
        result = fetch_logs()
    assert result["error"] == "Docker socket unavailable"


def test_parse_line_json():
    line = '{"timestamp": "2026-07-13T17:00:00+00:00", "service": "analysis", "level": "ERROR", "message": "oops"}'
    entry = _parse_line(line, "analysis")
    assert entry["level"] == "ERROR"
    assert entry["message"] == "oops"
    assert entry["service"] == "analysis"


def test_parse_line_non_json_falls_back():
    entry = _parse_line("plain text log line", "data")
    assert entry["service"] == "data"
    assert entry["message"] == "plain text log line"
    assert entry["level"] == "INFO"


def test_services_list():
    assert SERVICES == ["analysis", "data", "execution", "dashboard", "ml", "alerts", "research", "watchdog"]


def _wlog(level, message, seconds_ago=5):
    ts = datetime.fromtimestamp(time.time() - seconds_ago, tz=timezone.utc).isoformat()
    return json.dumps({"timestamp": ts, "level": level, "message": message})


def test_fetch_watchdog_logs_returns_entries(tmp_path):
    log_file = tmp_path / "watchdog.log"
    log_file.write_text(_wlog("INFO", "Watchdog starting") + "\n")
    result = _fetch_watchdog_logs(str(log_file), since_seconds=60)
    assert len(result) == 1
    assert result[0]["service"] == "watchdog"
    assert result[0]["message"] == "Watchdog starting"
    assert result[0]["level"] == "INFO"


def test_fetch_watchdog_logs_missing_file_returns_empty():
    result = _fetch_watchdog_logs("/nonexistent/path/watchdog.log", since_seconds=60)
    assert result == []


def test_fetch_watchdog_logs_empty_file_returns_empty(tmp_path):
    log_file = tmp_path / "watchdog.log"
    log_file.write_text("")
    result = _fetch_watchdog_logs(str(log_file), since_seconds=60)
    assert result == []


def test_fetch_watchdog_logs_filters_old_entries(tmp_path):
    log_file = tmp_path / "watchdog.log"
    recent = _wlog("INFO", "recent", seconds_ago=10)
    old = _wlog("WARNING", "old entry", seconds_ago=7200)
    log_file.write_text(old + "\n" + recent + "\n")
    result = _fetch_watchdog_logs(str(log_file), since_seconds=60)
    assert len(result) == 1
    assert result[0]["message"] == "recent"


def test_fetch_watchdog_logs_plain_text_fallback(tmp_path):
    log_file = tmp_path / "watchdog.log"
    log_file.write_text("not valid json\n")
    result = _fetch_watchdog_logs(str(log_file), since_seconds=60)
    assert len(result) == 1
    assert result[0]["service"] == "watchdog"
    assert result[0]["level"] == "INFO"
    assert result[0]["message"] == "not valid json"


def test_fetch_logs_includes_watchdog_entries(tmp_path):
    log_file = tmp_path / "watchdog.log"
    log_file.write_text(_wlog("ERROR", "watchdog error") + "\n")
    mock_docker = MagicMock()
    mock_docker.containers.get.side_effect = docker_module.errors.NotFound("nope")
    with patch("log_reader.docker.DockerClient", return_value=mock_docker), \
         patch("log_reader.load_config", return_value={"watchdog": {"log_file": str(log_file)}}):
        result = fetch_logs(services=["watchdog"])
    assert result["showing"] == 1
    assert result["logs"][0]["service"] == "watchdog"


def test_fetch_logs_excludes_watchdog_when_not_in_services(tmp_path):
    log_file = tmp_path / "watchdog.log"
    log_file.write_text(_wlog("ERROR", "watchdog error") + "\n")
    mock_docker = _mock_client({"analysis": [_make_log("analysis", "INFO", "ok")]})
    with patch("log_reader.docker.DockerClient", return_value=mock_docker):
        result = fetch_logs(services=["analysis"])
    assert all(e["service"] != "watchdog" for e in result["logs"])
