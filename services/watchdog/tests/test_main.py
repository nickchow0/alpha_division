import pytest
from unittest.mock import patch, MagicMock, call

from main import run_once

_CFG = {
    "watchdog": {
        "poll_interval_seconds": 30,
        "error_cooldown_minutes": 10,
        "restart_limit": 3,
        "restart_window_minutes": 30,
        "suppression_minutes": 60,
        "confidence_threshold": 0.7,
        "ollama_model": "deepseek-r1:7b",
        "ollama_base_url": "http://localhost:11434",
        "compose_file": "/opt/alphadivision/docker-compose.yml",
        "state_file": "/tmp/test_watchdog_main_state.json",
        "log_file": "/tmp/test_watchdog.log",
    }
}

_ERRORS = [
    {"service": "analysis", "message": "Connection refused port 11434", "line": "..."},
]

_CLASSIFICATION = {
    "action": "restart_service", "target": "analysis",
    "reasoning": "Ollama down", "confidence": 0.9,
}


def test_run_once_processes_new_errors():
    with patch("main.collect_errors", return_value=_ERRORS), \
         patch("main.is_suppressed", return_value=False), \
         patch("main.record_seen"), \
         patch("main.classify_error", return_value=_CLASSIFICATION), \
         patch("main.run_action", return_value="executed") as mock_action:
        run_once(_CFG)
    mock_action.assert_called_once()


def test_run_once_skips_suppressed_errors():
    with patch("main.collect_errors", return_value=_ERRORS), \
         patch("main.is_suppressed", return_value=True), \
         patch("main.classify_error") as mock_classify:
        run_once(_CFG)
    mock_classify.assert_not_called()


def test_run_once_records_seen_before_classifying():
    seen_order = []
    with patch("main.collect_errors", return_value=_ERRORS), \
         patch("main.is_suppressed", return_value=False), \
         patch("main.record_seen", side_effect=lambda *a: seen_order.append("seen")), \
         patch("main.classify_error", side_effect=lambda *a, **kw: seen_order.append("classify") or _CLASSIFICATION), \
         patch("main.run_action", return_value="executed"):
        run_once(_CFG)
    assert seen_order == ["seen", "classify"]


def test_run_once_handles_empty_errors():
    with patch("main.collect_errors", return_value=[]), \
         patch("main.classify_error") as mock_classify:
        run_once(_CFG)
    mock_classify.assert_not_called()


def test_run_once_handles_classify_exception():
    with patch("main.collect_errors", return_value=_ERRORS), \
         patch("main.is_suppressed", return_value=False), \
         patch("main.record_seen"), \
         patch("main.classify_error", side_effect=Exception("Ollama down")), \
         patch("main.send_notification") as mock_notify:
        run_once(_CFG)  # must not raise
    mock_notify.assert_called_once()


import json
import os
import logging

from main import _JsonFormatter, _add_file_handler


def test_json_formatter_produces_valid_json():
    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="watchdog", level=logging.INFO, pathname="", lineno=0,
        msg="Watchdog starting", args=(), exc_info=None,
    )
    line = formatter.format(record)
    parsed = json.loads(line)
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "Watchdog starting"
    assert "timestamp" in parsed


def test_json_formatter_error_level():
    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="watchdog", level=logging.ERROR, pathname="", lineno=0,
        msg="something failed", args=(), exc_info=None,
    )
    parsed = json.loads(formatter.format(record))
    assert parsed["level"] == "ERROR"


def test_add_file_handler_attaches_to_root_logger(tmp_path):
    log_file = str(tmp_path / "watchdog.log")
    root = logging.getLogger()
    initial_count = len(root.handlers)
    _add_file_handler(log_file)
    assert len(root.handlers) == initial_count + 1
    # Clean up
    handler = root.handlers[-1]
    root.removeHandler(handler)
    handler.close()


def test_add_file_handler_writes_json_on_log(tmp_path):
    log_file = str(tmp_path / "watchdog.log")
    root = logging.getLogger()
    _add_file_handler(log_file)
    logging.getLogger("watchdog").info("test message from handler")
    handler = root.handlers[-1]
    handler.flush()
    root.removeHandler(handler)
    handler.close()
    with open(log_file) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert len(lines) >= 1
    parsed = json.loads(lines[-1])
    assert parsed["message"] == "test message from handler"
    assert parsed["level"] == "INFO"
