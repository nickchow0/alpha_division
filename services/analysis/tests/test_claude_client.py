import pytest
from unittest.mock import patch, MagicMock

from claude_client import build_prompt, call_claude, MODEL_HAIKU


def _sample_snapshot() -> dict:
    return {
        "symbol": "AAPL",
        "price": 175.50,
        "rsi": 52.3,
        "sma20": 172.1,
        "sma50": 168.5,
        "sma20_prev": 171.8,
        "sma20_prev2": 171.5,
        "news": [
            {"headline": "Apple reports record earnings", "datetime": 1715000000},
            {"headline": "iPhone 18 demand strong", "datetime": 1714990000},
        ],
        "macro": {"fed_funds_rate": 5.33, "cpi": 314.5},
    }


def _make_claude_response(input_dict: dict):
    """Build a mock Anthropic message response with a ToolUseBlock."""
    mock_tool_block = MagicMock()
    mock_tool_block.input = input_dict
    mock_message = MagicMock()
    mock_message.content = [mock_tool_block]
    return mock_message


# ---------------------------------------------------------------------------
# build_prompt tests
# ---------------------------------------------------------------------------

def test_build_prompt_includes_symbol():
    prompt = build_prompt(_sample_snapshot())
    assert "AAPL" in prompt


def test_build_prompt_includes_price_and_indicators():
    prompt = build_prompt(_sample_snapshot())
    assert "175.50" in prompt
    assert "52.3" in prompt   # RSI
    assert "172.1" in prompt  # SMA20
    assert "168.5" in prompt  # SMA50


def test_build_prompt_includes_news_headlines():
    prompt = build_prompt(_sample_snapshot())
    assert "Apple reports record earnings" in prompt
    assert "iPhone 18 demand strong" in prompt


def test_build_prompt_handles_empty_news():
    snapshot = _sample_snapshot()
    snapshot["news"] = []
    prompt = build_prompt(snapshot)
    assert "No recent news" in prompt


def test_build_prompt_includes_macro_data():
    prompt = build_prompt(_sample_snapshot())
    assert "5.33" in prompt
    assert "314.5" in prompt


def test_build_prompt_asks_for_trading_recommendation():
    prompt = build_prompt(_sample_snapshot())
    assert "decision" in prompt.lower() or "recommendation" in prompt.lower()


def test_decision_tool_schema_has_required_fields():
    from claude_client import _DECISION_TOOL
    schema = _DECISION_TOOL["input_schema"]
    assert set(schema["required"]) == {"decision", "confidence", "reasoning"}
    enum = schema["properties"]["decision"]["enum"]
    for expected in ("buy", "sell", "short", "cover", "hold"):
        assert expected in enum


def test_build_prompt_includes_long_position_context():
    prompt = build_prompt(_sample_snapshot(), position_direction="long")
    assert "LONG" in prompt
    assert "SELL" in prompt or "sell" in prompt.lower()


def test_build_prompt_includes_short_position_context():
    prompt = build_prompt(_sample_snapshot(), position_direction="short")
    assert "SHORT" in prompt
    assert "COVER" in prompt or "cover" in prompt.lower()


def test_build_prompt_no_position_includes_short_option():
    prompt = build_prompt(_sample_snapshot(), position_direction=None)
    assert "short" in prompt.lower() or "SHORT" in prompt


# ---------------------------------------------------------------------------
# call_claude tests
# ---------------------------------------------------------------------------

def test_call_claude_returns_parsed_decision():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _make_claude_response(
            {"decision": "buy", "confidence": 0.78, "reasoning": "Strong momentum."}
        )
        result = call_claude(_sample_snapshot(), "test-api-key")

    assert result["decision"] == "buy"
    assert result["confidence"] == pytest.approx(0.78)
    assert "reasoning" in result
    assert "model" in result


def test_call_claude_uses_haiku_by_default():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = _make_claude_response(
            {"decision": "hold", "confidence": 0.5, "reasoning": "Neutral."}
        )
        call_claude(_sample_snapshot(), "test-api-key")

    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["model"] == MODEL_HAIKU


def test_call_claude_uses_tool_choice_forced():
    """messages.create must be called with tool_choice forcing record_decision."""
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = _make_claude_response(
            {"decision": "hold", "confidence": 0.5, "reasoning": "Neutral."}
        )
        call_claude(_sample_snapshot(), "test-api-key")

    call_kwargs = mock_create.call_args[1]
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "record_decision"}
    assert any(t["name"] == "record_decision" for t in call_kwargs["tools"])


def test_call_claude_accepts_custom_model():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        mock_create = MockClient.return_value.messages.create
        mock_create.return_value = _make_claude_response(
            {"decision": "sell", "confidence": 0.8, "reasoning": "Downtrend."}
        )
        result = call_claude(_sample_snapshot(), "test-api-key", model="claude-sonnet-4-5")

    assert result["model"] == "claude-sonnet-4-5"
    assert mock_create.call_args[1]["model"] == "claude-sonnet-4-5"


def test_call_claude_raises_on_missing_decision_field():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _make_claude_response(
            {"confidence": 0.7, "reasoning": "Missing decision."}
        )
        with pytest.raises(ValueError, match="missing field 'decision'"):
            call_claude(_sample_snapshot(), "test-api-key")


def test_call_claude_raises_on_missing_confidence_field():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _make_claude_response(
            {"decision": "buy", "reasoning": "Missing confidence."}
        )
        with pytest.raises(ValueError, match="missing field 'confidence'"):
            call_claude(_sample_snapshot(), "test-api-key")


def test_call_claude_raises_on_missing_reasoning_field():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _make_claude_response(
            {"decision": "buy", "confidence": 0.7}
        )
        with pytest.raises(ValueError, match="missing field 'reasoning'"):
            call_claude(_sample_snapshot(), "test-api-key")


def test_call_claude_raises_on_invalid_decision_value():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _make_claude_response(
            {"decision": "maybe", "confidence": 0.6, "reasoning": "Unsure."}
        )
        with pytest.raises(ValueError, match="Invalid decision"):
            call_claude(_sample_snapshot(), "test-api-key")


def test_call_claude_accepts_short_decision():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _make_claude_response(
            {"decision": "short", "confidence": 0.75, "reasoning": "Overbought."}
        )
        result = call_claude(_sample_snapshot(), "test-api-key")
    assert result["decision"] == "short"


def test_call_claude_accepts_cover_decision():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _make_claude_response(
            {"decision": "cover", "confidence": 0.7, "reasoning": "Oversold reversal."}
        )
        result = call_claude(_sample_snapshot(), "test-api-key")
    assert result["decision"] == "cover"


def test_call_claude_raises_on_confidence_out_of_range():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _make_claude_response(
            {"decision": "buy", "confidence": 1.5, "reasoning": "Very confident."}
        )
        with pytest.raises(ValueError, match="out of range"):
            call_claude(_sample_snapshot(), "test-api-key")


def test_call_claude_propagates_anthropic_api_errors():
    with patch("claude_client.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.side_effect = Exception("Connection refused")
        with pytest.raises(Exception, match="Connection refused"):
            call_claude(_sample_snapshot(), "test-api-key")
