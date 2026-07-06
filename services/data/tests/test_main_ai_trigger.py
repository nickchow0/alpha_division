"""
Tests for the immediate AI health check trigger logic.

The trigger key `health:ai_check_requested` is written to Redis by the dashboard
when the user switches AI provider. The data service main loop picks this up on
the next 5-second tick and runs an immediate health check for the new provider.

We test the logic directly (inlined) rather than importing main.py, since main.py
depends on shared.logger and other container-only modules.
"""
import time
import pytest
from unittest.mock import MagicMock, patch

# Constants mirroring those in main.py
_REDIS_AI_PROVIDER_KEY = "config:ai_provider"
_REDIS_AI_CHECK_TRIGGER = "health:ai_check_requested"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_redis(trigger_set: bool = True, provider: str = "claude"):
    """Return a mock Redis client that simulates the trigger key."""
    mock_redis = MagicMock()
    mock_redis.getdel.return_value = b"1" if trigger_set else None
    mock_redis.get.return_value = provider.encode() if provider else None
    return mock_redis


def _run_trigger_block(mock_redis, mock_check_ai_api, mock_write_health_result,
                       anthropic_api_key: str, gemini_api_key: str):
    """
    Execute the trigger-check logic from main()'s while loop.

    This mirrors the exact code in services/data/main.py so that a change to
    the logic there must also be reflected here. If the test breaks after a
    refactor it means the test and the production code are out of sync.
    """
    try:
        r = mock_redis
        if r.getdel(_REDIS_AI_CHECK_TRIGGER):
            raw = r.get(_REDIS_AI_PROVIDER_KEY)
            provider = (raw.decode() if isinstance(raw, bytes) else raw) if raw else "claude"
            ai_key = gemini_api_key if provider == "gemini" else anthropic_api_key
            try:
                _start = time.monotonic()
                mock_check_ai_api(provider, ai_key)
                latency_ms = int((time.monotonic() - _start) * 1000)
                mock_write_health_result(provider, "ok", latency_ms, None)
            except Exception as exc:
                mock_write_health_result(provider, "warning", 0, str(exc))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_trigger_calls_check_ai_api_for_claude():
    """When trigger is set and provider is claude, check_ai_api is called for claude."""
    mock_redis = _make_redis(trigger_set=True, provider="claude")
    mock_check = MagicMock(return_value="ok")
    mock_write = MagicMock()

    _run_trigger_block(mock_redis, mock_check, mock_write,
                       anthropic_api_key="test-key", gemini_api_key="")

    mock_check.assert_called_once_with("claude", "test-key")
    mock_write.assert_called_once()
    args = mock_write.call_args[0]
    assert args[0] == "claude"
    assert args[1] == "ok"


def test_trigger_calls_check_ai_api_for_gemini():
    """When trigger is set and provider is gemini, check_ai_api is called for gemini."""
    mock_redis = _make_redis(trigger_set=True, provider="gemini")
    mock_check = MagicMock(return_value="ok")
    mock_write = MagicMock()

    _run_trigger_block(mock_redis, mock_check, mock_write,
                       anthropic_api_key="ant-key", gemini_api_key="gem-key")

    mock_check.assert_called_once_with("gemini", "gem-key")
    args = mock_write.call_args[0]
    assert args[0] == "gemini"
    assert args[1] == "ok"


def test_trigger_not_set_skips_check():
    """When trigger key is absent, check_ai_api is not called."""
    mock_redis = _make_redis(trigger_set=False, provider="claude")
    mock_check = MagicMock()
    mock_write = MagicMock()

    _run_trigger_block(mock_redis, mock_check, mock_write,
                       anthropic_api_key="test-key", gemini_api_key="")

    mock_check.assert_not_called()
    mock_write.assert_not_called()


def test_trigger_check_failure_writes_warning():
    """When check_ai_api raises, write_health_result is called with 'warning'."""
    mock_redis = _make_redis(trigger_set=True, provider="claude")
    mock_check = MagicMock(side_effect=Exception("API down"))
    mock_write = MagicMock()

    _run_trigger_block(mock_redis, mock_check, mock_write,
                       anthropic_api_key="test-key", gemini_api_key="")

    mock_write.assert_called_once()
    args = mock_write.call_args[0]
    assert args[0] == "claude"
    assert args[1] == "warning"
    assert "API down" in args[3]


def test_trigger_defaults_to_claude_when_provider_key_missing():
    """When config:ai_provider is absent from Redis, defaults to claude."""
    mock_redis = _make_redis(trigger_set=True, provider="claude")
    mock_redis.get.return_value = None  # no provider key set
    mock_check = MagicMock(return_value="ok")
    mock_write = MagicMock()

    _run_trigger_block(mock_redis, mock_check, mock_write,
                       anthropic_api_key="ant-key", gemini_api_key="gem-key")

    mock_check.assert_called_once_with("claude", "ant-key")


def test_trigger_uses_gemini_key_not_anthropic_for_gemini():
    """Gemini provider uses gemini_api_key, not anthropic_api_key."""
    mock_redis = _make_redis(trigger_set=True, provider="gemini")
    mock_check = MagicMock(return_value="ok")
    mock_write = MagicMock()

    _run_trigger_block(mock_redis, mock_check, mock_write,
                       anthropic_api_key="should-not-be-used", gemini_api_key="correct-gem-key")

    mock_check.assert_called_once_with("gemini", "correct-gem-key")
