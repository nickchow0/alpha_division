import json
import pytest
from unittest.mock import patch, MagicMock

from gemini_client import call_gemini, MODEL_FLASH


def _sample_snapshot() -> dict:
    return {
        "symbol": "AAPL",
        "price": 175.50,
        "rsi": 52.3,
        "sma20": 172.1,
        "sma50": 168.5,
        "sma20_prev": 171.8,
        "sma20_prev2": 171.5,
        "news": [{"headline": "Apple reports record earnings", "datetime": 1715000000}],
        "macro": {"fed_funds_rate": 5.33, "cpi": 314.5},
    }


def _make_gemini_response(text: str):
    mock_resp = MagicMock()
    mock_resp.text = text
    return mock_resp


# ---------------------------------------------------------------------------
# call_gemini tests
# ---------------------------------------------------------------------------

def test_call_gemini_returns_parsed_decision():
    response_json = json.dumps({
        "decision": "buy",
        "confidence": 0.78,
        "reasoning": "Strong momentum with rising SMA20.",
    })
    with patch("gemini_client.genai.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = _make_gemini_response(response_json)
        result = call_gemini(_sample_snapshot(), "test-gemini-key")

    assert result["decision"] == "buy"
    assert result["confidence"] == pytest.approx(0.78)
    assert "reasoning" in result
    assert "model" in result


def test_call_gemini_uses_flash_by_default():
    response_json = json.dumps({"decision": "hold", "confidence": 0.5, "reasoning": "Neutral."})
    with patch("gemini_client.genai.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = _make_gemini_response(response_json)
        call_gemini(_sample_snapshot(), "test-gemini-key")
    MockModel.assert_called_once_with(MODEL_FLASH)


def test_call_gemini_accepts_custom_model():
    response_json = json.dumps({"decision": "sell", "confidence": 0.8, "reasoning": "Downtrend."})
    with patch("gemini_client.genai.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = _make_gemini_response(response_json)
        result = call_gemini(_sample_snapshot(), "test-gemini-key", model="gemini-1.5-pro")

    assert result["model"] == "gemini-1.5-pro"
    MockModel.assert_called_once_with("gemini-1.5-pro")


def test_call_gemini_raises_on_non_json_response():
    with patch("gemini_client.genai.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = _make_gemini_response("I cannot help.")
        with pytest.raises(ValueError, match="not valid JSON"):
            call_gemini(_sample_snapshot(), "test-gemini-key")


def test_call_gemini_strips_markdown_code_fences():
    inner = json.dumps({"decision": "buy", "confidence": 0.72, "reasoning": "Strong setup."})
    fenced = f"```json\n{inner}\n```"
    with patch("gemini_client.genai.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = _make_gemini_response(fenced)
        result = call_gemini(_sample_snapshot(), "test-gemini-key")

    assert result["decision"] == "buy"


def test_call_gemini_raises_on_missing_field():
    response_json = json.dumps({"confidence": 0.7, "reasoning": "Missing decision."})
    with patch("gemini_client.genai.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = _make_gemini_response(response_json)
        with pytest.raises(ValueError, match="missing field 'decision'"):
            call_gemini(_sample_snapshot(), "test-gemini-key")


def test_call_gemini_raises_on_invalid_decision_value():
    response_json = json.dumps({"decision": "maybe", "confidence": 0.6, "reasoning": "Unsure."})
    with patch("gemini_client.genai.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = _make_gemini_response(response_json)
        with pytest.raises(ValueError, match="Invalid decision"):
            call_gemini(_sample_snapshot(), "test-gemini-key")


def test_call_gemini_raises_on_confidence_out_of_range():
    response_json = json.dumps({"decision": "buy", "confidence": 1.5, "reasoning": "Very confident."})
    with patch("gemini_client.genai.GenerativeModel") as MockModel:
        MockModel.return_value.generate_content.return_value = _make_gemini_response(response_json)
        with pytest.raises(ValueError, match="out of range"):
            call_gemini(_sample_snapshot(), "test-gemini-key")


def test_call_gemini_configures_api_key():
    response_json = json.dumps({"decision": "hold", "confidence": 0.5, "reasoning": "Flat."})
    with patch("gemini_client.genai.GenerativeModel") as MockModel, \
         patch("gemini_client.genai.configure") as mock_configure:
        MockModel.return_value.generate_content.return_value = _make_gemini_response(response_json)
        call_gemini(_sample_snapshot(), "my-secret-key")

    mock_configure.assert_called_once_with(api_key="my-secret-key")
