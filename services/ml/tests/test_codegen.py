"""Unit tests for codegen.py — Claude API mocked throughout."""
import ast
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from unittest.mock import MagicMock, patch
import pytest

from discoverer import CandidatePattern
from codegen import generate_strategy_code, _validate_code, _build_prompt, _call_gemini


_VALID_CODE = '''
def generate_signal(snapshot):
    price = snapshot["price"]
    rsi = snapshot["rsi_14"]
    if rsi < 40:
        return {"decision": "buy", "confidence": 0.7, "reasoning": "RSI oversold"}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "No signal"}
'''

_INVALID_CODE_NO_FUNCTION = "x = 1 + 2"
_INVALID_CODE_SYNTAX = "def generate_signal(snapshot: invalid syntax!!!"
_INVALID_CODE_BAD_SCHEMA = '''
def generate_signal(snapshot):
    return "buy"  # wrong return type
'''


def _make_pattern(rule="RSI_14 <= 38.0 AND vol_zscore > 1.3") -> CandidatePattern:
    return CandidatePattern(
        pattern_type="decision_tree",
        rule_description=rule,
        example_count=45,
        avg_forward_return_pct=2.3,
        win_rate_pct=55.0,
        sharpe=0.8,
        symbol="CRWD",
    )


def _mock_anthropic_response(code: str):
    """Return a mock Anthropic API response containing the given code."""
    msg = MagicMock()
    msg.content = [MagicMock(text=f"```python\n{code}\n```")]
    return msg


def test_validate_code_accepts_valid_function():
    errors = _validate_code(_VALID_CODE)
    assert errors == []


def test_validate_code_rejects_syntax_error():
    errors = _validate_code(_INVALID_CODE_SYNTAX)
    assert len(errors) > 0
    assert any("syntax" in e.lower() or "parse" in e.lower() for e in errors)


def test_validate_code_rejects_missing_function():
    errors = _validate_code(_INVALID_CODE_NO_FUNCTION)
    assert any("generate_signal" in e for e in errors)


def test_validate_code_rejects_bad_schema():
    errors = _validate_code(_INVALID_CODE_BAD_SCHEMA)
    assert len(errors) > 0


def test_build_prompt_contains_pattern_info():
    pattern = _make_pattern()
    prompt = _build_prompt(pattern)
    assert "RSI_14" in prompt
    assert "2.3" in prompt  # avg return
    assert "55.0" in prompt  # win rate
    assert "generate_signal" in prompt


def test_generate_strategy_code_success():
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(_VALID_CODE)

    result = generate_strategy_code(pattern, client=mock_client)

    assert result is not None
    assert "generate_signal" in result
    mock_client.messages.create.assert_called_once()


def test_generate_strategy_code_retries_once_on_invalid():
    """If first response is invalid, retries once with error context."""
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _mock_anthropic_response(_INVALID_CODE_NO_FUNCTION),
        _mock_anthropic_response(_VALID_CODE),
    ]

    result = generate_strategy_code(pattern, client=mock_client)

    assert result is not None
    assert mock_client.messages.create.call_count == 2


def test_generate_strategy_code_returns_none_on_double_failure():
    """If both attempts produce invalid code, returns None."""
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(
        _INVALID_CODE_NO_FUNCTION
    )

    result = generate_strategy_code(pattern, client=mock_client)

    assert result is None
    assert mock_client.messages.create.call_count == 2


def test_generate_strategy_code_strips_markdown_fences():
    """Code returned inside ```python ... ``` blocks is extracted correctly."""
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(_VALID_CODE)

    result = generate_strategy_code(pattern, client=mock_client)
    assert "```" not in (result or "")


def test_validate_code_accepts_code_with_common_builtins():
    """Code using min/max/abs/round/isinstance should not be rejected."""
    code_with_builtins = '''
def generate_signal(snapshot):
    price = float(snapshot["price"])
    rsi = snapshot["rsi_14"]
    vol = snapshot["volume"]
    vol_ratio = snapshot.get("vol_ratio") or 1.0
    if rsi < 40 and vol_ratio > 1.2:
        conf = min(0.9, abs(rsi - 50) / 50)
        return {"decision": "buy", "confidence": conf, "reasoning": f"RSI={rsi:.1f}"}
    return {"decision": "hold", "confidence": 0.5, "reasoning": "no signal"}
'''
    errors = _validate_code(code_with_builtins)
    assert errors == [], f"Unexpected errors: {errors}"


def test_build_prompt_contains_ml_snapshot_keys():
    pattern = _make_pattern()
    prompt = _build_prompt(pattern)
    for key in ("rsi_14", "macd_hist", "dist_sma20", "vol_zscore", "atr_14"):
        assert key in prompt, f"Prompt missing key: {key}"


def test_build_prompt_does_not_mention_volume_avg():
    """volume_avg is not a real snapshot key — it must not appear in the prompt."""
    pattern = _make_pattern()
    prompt = _build_prompt(pattern)
    assert "volume_avg" not in prompt


def _mock_gemini_response(code: str):
    """Return a mock Gemini API response containing the given code."""
    resp = MagicMock()
    resp.text = f"```python\n{code}\n```"
    return resp


def test_call_gemini_returns_response_text():
    with patch("codegen.genai.configure") as mock_cfg, \
         patch("codegen.genai.GenerativeModel") as MockModel:
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = _mock_gemini_response(_VALID_CODE)
        MockModel.return_value = mock_instance

        result = _call_gemini("test prompt", api_key="fake-key", model="gemini-2.0-flash")

        mock_cfg.assert_called_once_with(api_key="fake-key")
        MockModel.assert_called_once_with("gemini-2.0-flash")
        assert "generate_signal" in result


def test_generate_strategy_code_gemini_success():
    pattern = _make_pattern()
    with patch("codegen.genai.configure"), \
         patch("codegen.genai.GenerativeModel") as MockModel:
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = _mock_gemini_response(_VALID_CODE)
        MockModel.return_value = mock_instance

        result = generate_strategy_code(
            pattern, provider="gemini", model="gemini-2.0-flash", gemini_api_key="fake-key"
        )

    assert result is not None
    assert "generate_signal" in result


def test_generate_strategy_code_gemini_retries_once_on_invalid():
    pattern = _make_pattern()
    with patch("codegen.genai.configure"), \
         patch("codegen.genai.GenerativeModel") as MockModel:
        mock_instance = MagicMock()
        mock_instance.generate_content.side_effect = [
            _mock_gemini_response(_INVALID_CODE_NO_FUNCTION),
            _mock_gemini_response(_VALID_CODE),
        ]
        MockModel.return_value = mock_instance

        result = generate_strategy_code(
            pattern, provider="gemini", model="gemini-2.0-flash", gemini_api_key="fake-key"
        )

    assert result is not None
    assert mock_instance.generate_content.call_count == 2


def test_generate_strategy_code_claude_unchanged_with_new_params():
    """Existing Claude path still works when provider/model are explicitly passed."""
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(_VALID_CODE)

    result = generate_strategy_code(
        pattern, client=mock_client, provider="claude", model="claude-sonnet-4-5"
    )

    assert result is not None
    mock_client.messages.create.assert_called_once()
