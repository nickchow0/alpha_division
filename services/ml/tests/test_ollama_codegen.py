import pytest
from unittest.mock import patch, MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ollama_codegen import call_ollama_codegen

def _mock_post(content: str):
    mock = MagicMock()
    mock.json.return_value = {"choices": [{"message": {"content": content}}]}
    mock.raise_for_status.return_value = None
    return mock

def test_call_ollama_codegen_returns_raw_text():
    expected = "```python\ndef generate_signal(snapshot):\n    return {'decision': 'hold', 'confidence': 0.5, 'reasoning': 'ok'}\n```"
    with patch("ollama_codegen.requests.post", return_value=_mock_post(expected)):
        result = call_ollama_codegen("some prompt", "http://localhost:11434", "qwen2.5-coder:7b")
    assert result == expected

def test_call_ollama_codegen_sends_correct_payload():
    with patch("ollama_codegen.requests.post", return_value=_mock_post("code")) as mock_post:
        call_ollama_codegen("test prompt", "http://localhost:11434", "qwen2.5-coder:7b")
    body = mock_post.call_args[1]["json"]
    assert body["model"] == "qwen2.5-coder:7b"
    assert body["messages"][0]["content"] == "test prompt"
    assert body["stream"] is False

def test_call_ollama_codegen_raises_on_http_error():
    mock = MagicMock()
    mock.raise_for_status.side_effect = Exception("503 Service Unavailable")
    with patch("ollama_codegen.requests.post", return_value=mock):
        with pytest.raises(Exception, match="503"):
            call_ollama_codegen("prompt", "http://localhost:11434", "qwen2.5-coder:7b")
