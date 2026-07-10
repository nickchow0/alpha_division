"""
Tests for threshold selection in _process_snapshot based on whether the AI
result came from the primary model or the Ollama fallback.

Task 5: Analysis Confidence Threshold for Fallback
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/opt/alphadivision")

from main import _process_snapshot


def _snapshot(**overrides) -> dict:
    """Build a minimal snapshot dict that passes Stage 1 filters."""
    base = {
        "symbol": "AAPL",
        "price": 175.0,
        "rsi": 52.0,
        "sma20": 170.0,
        "sma50": 160.0,
        "sma20_prev": 169.0,
        "sma20_prev2": 168.0,
        "news": [],
        "macro": {"fed_funds_rate": 5.33, "cpi": 314.5},
    }
    base.update(overrides)
    return base


def _config_with_ollama_threshold(threshold=0.75) -> dict:
    return {
        "analysis": {
            "ai_provider": "gemini",
            "gemini_model": "gemini-2.5-flash",
            "ollama_model": "qwen2.5:7b",
            "ollama_base_url": "http://localhost:11434",
            "ollama_confidence_threshold": threshold,
        }
    }


def test_fallback_result_uses_ollama_confidence_threshold():
    """
    A fallback decision with confidence 0.70 should be held (below ollama threshold
    of 0.75) but would be acted on under the standard 0.65 threshold.
    """
    config = _config_with_ollama_threshold(0.75)
    fallback_result = {
        "decision": "buy",
        "confidence": 0.70,
        "reasoning": "local model says buy",
        "model": "ollama/qwen2.5:7b",
        "_via_fallback": True,
    }

    with patch("main.passes_technical_filter", return_value=(True, "")) as mock_tf, \
         patch("main.passes_short_filter", return_value=(False, "")) as mock_sf, \
         patch("main.call_ai", return_value=fallback_result) as mock_ai, \
         patch("main.write_decision", return_value=42) as mock_wd, \
         patch("main.write_signal") as mock_ws, \
         patch("main.send_alert") as mock_alert, \
         patch("main.ack_snapshot") as mock_ack:

        snapshot = _snapshot()
        _process_snapshot(snapshot, "anthro-key", "gemini-key", config, set(), set())

    # write_decision should have been called with acted_on=False
    mock_wd.assert_called_once()
    args = mock_wd.call_args[0]
    acted_on = args[5]  # 6th positional arg: symbol, decision, confidence, reasoning, model, acted_on, skip_reason
    skip_reason = args[6]
    assert acted_on is False, (
        f"Expected acted_on=False (confidence 0.70 is below ollama threshold 0.75) "
        f"but got acted_on={acted_on}"
    )
    assert skip_reason is not None, "Expected a skip_reason for low-confidence fallback"
    assert "0.70" in skip_reason or "0.7" in skip_reason, (
        f"Expected skip_reason to mention the confidence value, got: {skip_reason!r}"
    )

    # write_signal must NOT have been called
    mock_ws.assert_not_called()


def test_primary_result_uses_standard_threshold():
    """
    A primary (non-fallback) result with confidence 0.70 should be acted on
    because 0.70 >= standard threshold of 0.65.
    """
    config = _config_with_ollama_threshold(0.75)
    primary_result = {
        "decision": "buy",
        "confidence": 0.70,
        "reasoning": "gemini says buy",
        "model": "gemini-2.5-flash",
        # _via_fallback key intentionally absent
    }

    with patch("main.passes_technical_filter", return_value=(True, "")) as mock_tf, \
         patch("main.passes_short_filter", return_value=(False, "")) as mock_sf, \
         patch("main.call_ai", return_value=primary_result) as mock_ai, \
         patch("main.write_decision", return_value=42) as mock_wd, \
         patch("main.write_signal") as mock_ws, \
         patch("main.send_alert") as mock_alert, \
         patch("main.ack_snapshot") as mock_ack:

        snapshot = _snapshot()
        _process_snapshot(snapshot, "anthro-key", "gemini-key", config, set(), set())

    # write_decision should have been called with acted_on=True
    mock_wd.assert_called_once()
    args = mock_wd.call_args[0]
    acted_on = args[5]
    assert acted_on is True, (
        f"Expected acted_on=True (confidence 0.70 >= standard threshold 0.65) "
        f"but got acted_on={acted_on}"
    )

    # write_signal MUST have been called
    mock_ws.assert_called_once()
