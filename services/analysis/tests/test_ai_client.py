import pytest
from unittest.mock import patch, MagicMock

from ai_client import call_ai


def _sample_snapshot() -> dict:
    return {
        "symbol": "AAPL",
        "price": 175.50,
        "rsi": 52.3,
        "sma20": 172.1,
        "sma50": 168.5,
        "sma20_prev": 171.8,
        "sma20_prev2": 171.5,
        "news": [],
        "macro": {"fed_funds_rate": 5.33, "cpi": 314.5},
    }


def _decision(decision="buy", confidence=0.75, model="test-model"):
    return {"decision": decision, "confidence": confidence, "reasoning": "Test.", "model": model}


def test_call_ai_routes_to_claude_by_default():
    cfg = {"analysis": {"ai_provider": "claude", "claude_model": "claude-haiku-4-5"}}
    with patch("ai_client.call_claude", return_value=_decision()) as mock_claude:
        result = call_ai(_sample_snapshot(), cfg, anthropic_api_key="anthro-key")
    mock_claude.assert_called_once()
    assert result["decision"] == "buy"


def test_call_ai_routes_to_gemini_when_configured():
    cfg = {"analysis": {"ai_provider": "gemini", "gemini_model": "gemini-2.0-flash"}}
    with patch("ai_client.call_gemini", return_value=_decision(model="gemini-2.0-flash")) as mock_gemini:
        result = call_ai(_sample_snapshot(), cfg, gemini_api_key="gemini-key")
    mock_gemini.assert_called_once()
    assert result["model"] == "gemini-2.0-flash"


def test_call_ai_passes_claude_model_from_config():
    cfg = {"analysis": {"ai_provider": "claude", "claude_model": "claude-sonnet-4-5"}}
    with patch("ai_client.call_claude", return_value=_decision()) as mock_claude:
        call_ai(_sample_snapshot(), cfg, anthropic_api_key="key")
    _, kwargs = mock_claude.call_args
    assert kwargs["model"] == "claude-sonnet-4-5"


def test_call_ai_passes_gemini_model_from_config():
    cfg = {"analysis": {"ai_provider": "gemini", "gemini_model": "gemini-1.5-pro"}}
    with patch("ai_client.call_gemini", return_value=_decision()) as mock_gemini:
        call_ai(_sample_snapshot(), cfg, gemini_api_key="key")
    _, kwargs = mock_gemini.call_args
    assert kwargs["model"] == "gemini-1.5-pro"


def test_call_ai_raises_on_unknown_provider():
    cfg = {"analysis": {"ai_provider": "gpt4"}}
    with pytest.raises(ValueError, match="Unknown AI provider"):
        call_ai(_sample_snapshot(), cfg, anthropic_api_key="key")


def test_call_ai_defaults_to_claude_when_analysis_section_missing():
    cfg = {}  # no [analysis] section
    with patch("ai_client.call_claude", return_value=_decision()) as mock_claude:
        call_ai(_sample_snapshot(), cfg, anthropic_api_key="key")
    mock_claude.assert_called_once()


def test_call_ai_routes_to_ollama_when_configured_as_primary():
    cfg = {"analysis": {
        "ai_provider": "ollama",
        "ollama_model": "qwen2.5:7b",
        "ollama_base_url": "http://localhost:11434",
    }}
    with patch("ai_client.call_ollama", return_value={"decision": "buy", "confidence": 0.8, "reasoning": "ok", "model": "ollama/qwen2.5:7b"}) as mock_ollama:
        result = call_ai(_sample_snapshot(), cfg)
    mock_ollama.assert_called_once()
    assert result["model"] == "ollama/qwen2.5:7b"


def test_call_ai_falls_back_to_ollama_on_primary_failure():
    cfg = {"analysis": {
        "ai_provider": "gemini",
        "gemini_model": "gemini-2.5-flash",
        "ollama_model": "qwen2.5:7b",
        "ollama_base_url": "http://localhost:11434",
    }}
    with patch("ai_client.call_gemini", side_effect=Exception("quota exceeded")):
        with patch("ai_client.call_ollama", return_value={"decision": "buy", "confidence": 0.8, "reasoning": "ok", "model": "ollama/qwen2.5:7b"}) as mock_ollama:
            with patch("ai_client.send_alert"):
                result = call_ai(_sample_snapshot(), cfg, gemini_api_key="key")
    mock_ollama.assert_called_once()
    assert result["_via_fallback"] is True


def test_call_ai_no_fallback_when_ollama_model_empty():
    cfg = {"analysis": {
        "ai_provider": "gemini",
        "gemini_model": "gemini-2.5-flash",
        "ollama_model": "",
    }}
    with patch("ai_client.call_gemini", side_effect=Exception("quota exceeded")):
        with pytest.raises(Exception, match="quota exceeded"):
            call_ai(_sample_snapshot(), cfg, gemini_api_key="key")


def test_call_ai_raises_when_both_primary_and_ollama_fail():
    cfg = {"analysis": {
        "ai_provider": "gemini",
        "gemini_model": "gemini-2.5-flash",
        "ollama_model": "qwen2.5:7b",
        "ollama_base_url": "http://localhost:11434",
    }}
    with patch("ai_client.call_gemini", side_effect=Exception("primary down")):
        with patch("ai_client.call_ollama", side_effect=Exception("ollama down")):
            with patch("ai_client.send_alert"):
                with pytest.raises(Exception, match="ollama down"):
                    call_ai(_sample_snapshot(), cfg, gemini_api_key="key")


def test_call_ai_raises_when_ollama_primary_has_no_model():
    cfg = {"analysis": {"ai_provider": "ollama", "ollama_model": ""}}
    with pytest.raises(ValueError, match="ollama_model must be configured"):
        call_ai(_sample_snapshot(), cfg)


def test_call_ai_unknown_provider_error_lists_all_providers():
    cfg = {"analysis": {"ai_provider": "gpt4"}}
    with pytest.raises(ValueError, match="ollama"):
        call_ai(_sample_snapshot(), cfg)
