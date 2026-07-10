import json
import pytest
from unittest.mock import patch, MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ollama_client import call_ollama

_SNAPSHOT = {
    "symbol": "AAPL", "price": 150.0, "rsi": 55.0,
    "sma20": 148.0, "sma50": 145.0, "news": [], "macro": {},
}

def _mock_post(content: dict):
    mock = MagicMock()
    mock.json.return_value = {"choices": [{"message": {"content": json.dumps(content)}}]}
    mock.raise_for_status.return_value = None
    return mock

def test_call_ollama_happy_path():
    with patch("ollama_client.requests.post", return_value=_mock_post(
        {"decision": "buy", "confidence": 0.82, "reasoning": "RSI oversold"}
    )):
        result = call_ollama(_SNAPSHOT, "http://localhost:11434", "qwen2.5:7b")
    assert result["decision"] == "buy"
    assert result["confidence"] == 0.82
    assert result["reasoning"] == "RSI oversold"
    assert result["model"] == "ollama/qwen2.5:7b"

def test_call_ollama_sets_model_prefix():
    with patch("ollama_client.requests.post", return_value=_mock_post(
        {"decision": "hold", "confidence": 0.5, "reasoning": "neutral"}
    )):
        result = call_ollama(_SNAPSHOT, "http://localhost:11434", "llama3.1:8b")
    assert result["model"] == "ollama/llama3.1:8b"

def test_call_ollama_invalid_json_raises():
    mock = MagicMock()
    mock.json.return_value = {"choices": [{"message": {"content": "not json at all"}}]}
    mock.raise_for_status.return_value = None
    with patch("ollama_client.requests.post", return_value=mock):
        with pytest.raises(ValueError, match="not valid JSON"):
            call_ollama(_SNAPSHOT, "http://localhost:11434", "qwen2.5:7b")

def test_call_ollama_missing_decision_field_raises():
    with patch("ollama_client.requests.post", return_value=_mock_post(
        {"confidence": 0.7, "reasoning": "missing decision"}
    )):
        with pytest.raises(ValueError, match="missing field 'decision'"):
            call_ollama(_SNAPSHOT, "http://localhost:11434", "qwen2.5:7b")

def test_call_ollama_missing_confidence_field_raises():
    with patch("ollama_client.requests.post", return_value=_mock_post(
        {"decision": "buy", "reasoning": "no confidence"}
    )):
        with pytest.raises(ValueError, match="missing field 'confidence'"):
            call_ollama(_SNAPSHOT, "http://localhost:11434", "qwen2.5:7b")

def test_call_ollama_invalid_decision_raises():
    with patch("ollama_client.requests.post", return_value=_mock_post(
        {"decision": "maybe", "confidence": 0.6, "reasoning": "unsure"}
    )):
        with pytest.raises(ValueError, match="Invalid decision"):
            call_ollama(_SNAPSHOT, "http://localhost:11434", "qwen2.5:7b")

def test_call_ollama_confidence_above_1_raises():
    with patch("ollama_client.requests.post", return_value=_mock_post(
        {"decision": "hold", "confidence": 1.5, "reasoning": "too high"}
    )):
        with pytest.raises(ValueError, match="out of range"):
            call_ollama(_SNAPSHOT, "http://localhost:11434", "qwen2.5:7b")

def test_call_ollama_confidence_below_0_raises():
    with patch("ollama_client.requests.post", return_value=_mock_post(
        {"decision": "hold", "confidence": -0.1, "reasoning": "negative"}
    )):
        with pytest.raises(ValueError, match="out of range"):
            call_ollama(_SNAPSHOT, "http://localhost:11434", "qwen2.5:7b")

def test_call_ollama_passes_position_direction():
    with patch("ollama_client.requests.post", return_value=_mock_post(
        {"decision": "sell", "confidence": 0.8, "reasoning": "exit long"}
    )) as mock_post:
        call_ollama(_SNAPSHOT, "http://localhost:11434", "qwen2.5:7b", position_direction="long")
    body = mock_post.call_args[1]["json"]
    assert any("LONG" in str(m.get("content", "")) for m in body["messages"])
