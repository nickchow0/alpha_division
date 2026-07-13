import json
import pytest
from unittest.mock import patch, MagicMock

from error_classifier import classify_error


def _mock_ollama_response(content: dict):
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {
        "choices": [{"message": {"content": json.dumps(content)}}]
    }
    return mock


def test_classify_returns_restart_action():
    response = {
        "action": "restart_service",
        "target": "analysis",
        "reasoning": "Ollama connection refused — restart clears stale config",
        "confidence": 0.9,
    }
    with patch("error_classifier.requests.post", return_value=_mock_ollama_response(response)):
        result = classify_error("analysis", "Connection refused port 11434",
                                "http://localhost:11434", "deepseek-r1:7b")
    assert result["action"] == "restart_service"
    assert result["target"] == "analysis"
    assert result["confidence"] == 0.9


def test_classify_returns_no_action_for_transient():
    response = {
        "action": "no_action",
        "target": None,
        "reasoning": "Invalid HTTP request line is a scanner probe, not an app error",
        "confidence": 0.95,
    }
    with patch("error_classifier.requests.post", return_value=_mock_ollama_response(response)):
        result = classify_error("dashboard", "Invalid HTTP request line: ''",
                                "http://localhost:11434", "deepseek-r1:7b")
    assert result["action"] == "no_action"


def test_classify_falls_back_to_alert_only_on_invalid_json():
    mock = MagicMock()
    mock.raise_for_status.return_value = None
    mock.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
    with patch("error_classifier.requests.post", return_value=mock):
        result = classify_error("analysis", "some error",
                                "http://localhost:11434", "deepseek-r1:7b")
    assert result["action"] == "alert_only"
    assert result["confidence"] == 0.0


def test_classify_falls_back_to_alert_only_on_timeout():
    with patch("error_classifier.requests.post", side_effect=Exception("timeout")):
        result = classify_error("analysis", "some error",
                                "http://localhost:11434", "deepseek-r1:7b")
    assert result["action"] == "alert_only"
    assert result["confidence"] == 0.0


def test_classify_falls_back_on_missing_action_field():
    response = {"target": "analysis", "reasoning": "missing action", "confidence": 0.8}
    with patch("error_classifier.requests.post", return_value=_mock_ollama_response(response)):
        result = classify_error("analysis", "some error",
                                "http://localhost:11434", "deepseek-r1:7b")
    assert result["action"] == "alert_only"


def test_classify_sends_service_and_message_in_prompt():
    response = {"action": "no_action", "target": None, "reasoning": "ok", "confidence": 0.9}
    with patch("error_classifier.requests.post", return_value=_mock_ollama_response(response)) as mock_post:
        classify_error("ml", "strategy generation failed",
                       "http://localhost:11434", "deepseek-r1:7b")
    body = mock_post.call_args[1]["json"]
    user_content = next(m["content"] for m in body["messages"] if m["role"] == "user")
    assert "ml" in user_content
    assert "strategy generation failed" in user_content
