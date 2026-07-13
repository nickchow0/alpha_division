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
