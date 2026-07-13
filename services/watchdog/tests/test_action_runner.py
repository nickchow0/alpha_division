import pytest
from unittest.mock import patch, MagicMock, call

from action_runner import run_action

_CFG = {
    "watchdog": {
        "restart_limit": 3,
        "restart_window_minutes": 30,
        "suppression_minutes": 60,
        "confidence_threshold": 0.7,
        "compose_file": "/opt/alphadivision/docker-compose.yml",
        "state_file": "/tmp/test_watchdog_state.json",
    }
}

_ERROR = {"service": "analysis", "message": "Connection refused", "line": "analysis-1 | ERROR ..."}


def test_restart_service_executes_and_notifies():
    classification = {"action": "restart_service", "target": "analysis",
                      "reasoning": "stale config", "confidence": 0.9}
    with patch("action_runner.subprocess.run") as mock_run, \
         patch("action_runner.send_notification") as mock_notify, \
         patch("action_runner.restart_count_in_window", return_value=0), \
         patch("action_runner.record_restart"), \
         patch("action_runner.collect_errors", return_value=[]), \
         patch("action_runner.time.sleep"):
        mock_run.return_value.returncode = 0
        result = run_action(classification, _ERROR, _CFG)
    assert result == "executed"
    assert mock_run.called
    cmd = " ".join(mock_run.call_args[0][0])
    assert "restart" in cmd
    assert "analysis" in cmd
    assert mock_notify.called


def test_execution_service_always_alert_only():
    classification = {"action": "restart_service", "target": "execution",
                      "reasoning": "crashed", "confidence": 0.95}
    error = {"service": "execution", "message": "some error", "line": "..."}
    with patch("action_runner.send_notification") as mock_notify, \
         patch("action_runner.subprocess.run") as mock_run:
        result = run_action(classification, error, _CFG)
    assert result == "alert_only"
    assert not mock_run.called
    assert mock_notify.called
    msg = mock_notify.call_args[0][0]
    assert "human" in msg.lower() or "required" in msg.lower()


def test_low_confidence_overrides_to_alert_only():
    classification = {"action": "restart_service", "target": "analysis",
                      "reasoning": "maybe", "confidence": 0.5}
    with patch("action_runner.send_notification") as mock_notify, \
         patch("action_runner.subprocess.run") as mock_run:
        result = run_action(classification, _ERROR, _CFG)
    assert result == "alert_only"
    assert not mock_run.called


def test_restart_limit_triggers_escalation():
    classification = {"action": "restart_service", "target": "analysis",
                      "reasoning": "crashed", "confidence": 0.9}
    with patch("action_runner.restart_count_in_window", return_value=3), \
         patch("action_runner.send_notification") as mock_notify, \
         patch("action_runner.subprocess.run") as mock_run, \
         patch("action_runner.suppress_extended") as mock_suppress:
        result = run_action(classification, _ERROR, _CFG)
    assert result == "alert_only"
    assert not mock_run.called
    msg = mock_notify.call_args[0][0]
    assert "🚨" in msg
    assert mock_suppress.called


def test_no_action_returns_suppressed_silently():
    classification = {"action": "no_action", "target": None,
                      "reasoning": "scanner probe", "confidence": 0.95}
    with patch("action_runner.send_notification") as mock_notify:
        result = run_action(classification, _ERROR, _CFG)
    assert result == "no_action"
    assert not mock_notify.called


def test_verify_step_escalates_if_error_persists():
    classification = {"action": "restart_service", "target": "analysis",
                      "reasoning": "stale config", "confidence": 0.9}
    # Verify returns same error still present
    with patch("action_runner.subprocess.run") as mock_run, \
         patch("action_runner.send_notification") as mock_notify, \
         patch("action_runner.restart_count_in_window", return_value=0), \
         patch("action_runner.record_restart"), \
         patch("action_runner.collect_errors", return_value=[_ERROR]), \
         patch("action_runner.suppress_extended") as mock_suppress, \
         patch("action_runner.time.sleep"):
        mock_run.return_value.returncode = 0
        result = run_action(classification, _ERROR, _CFG)
    assert result == "executed"
    calls = [str(c) for c in mock_notify.call_args_list]
    assert any("persists" in c or "⚠️" in c for c in calls)
    assert mock_suppress.called


def test_unknown_target_is_alert_only():
    classification = {"action": "restart_service", "target": "redis",
                      "reasoning": "unknown", "confidence": 0.9}
    with patch("action_runner.send_notification") as mock_notify, \
         patch("action_runner.subprocess.run") as mock_run, \
         patch("action_runner.restart_count_in_window", return_value=0):
        result = run_action(classification, _ERROR, _CFG)
    assert result == "alert_only"
    assert not mock_run.called
