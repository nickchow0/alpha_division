import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock

import docker as docker_module
from log_reader import fetch_logs, _parse_line, SERVICES


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
    assert SERVICES == ["analysis", "data", "execution", "dashboard", "ml", "alerts", "research"]
