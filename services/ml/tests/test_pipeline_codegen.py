"""Tests for pipeline codegen settings reading in _run_phases."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from unittest.mock import patch, MagicMock, call
import pytest


def _make_mock_redis(provider=b"claude", claude_model=b"claude-sonnet-4-5", gemini_model=None):
    r = MagicMock()
    def _get(key):
        return {
            "config:ml_codegen_provider":     provider,
            "config:ml_codegen_claude_model": claude_model,
            "config:ml_codegen_gemini_model": gemini_model,
        }.get(key)
    r.get.side_effect = _get
    return r


@patch("pipeline.save_ml_run")
@patch("pipeline.save_ml_strategy")
@patch("pipeline.ensure_ml_tables")
@patch("pipeline._send_discord_alert")
@patch("pipeline.generate_strategy_code")
@patch("pipeline.discover_patterns")
@patch("pipeline.compute_features")
@patch("pipeline.collect_bars")
@patch("pipeline.get_redis")
@patch("pipeline.load_config")
def test_pipeline_passes_claude_settings_to_codegen(
    mock_cfg, mock_redis, mock_collect, mock_features,
    mock_discover, mock_codegen, mock_alert, mock_ensure,
    mock_save_strat, mock_save_run,
):
    from pipeline import _run_phases

    mock_cfg.return_value = {"ml": {
        "symbols": ["AAPL"],
        "lookback_days_momentum": 365,
        "lookback_days_regime": 1825,
        "max_strategies_per_run": 5,
        "min_forward_return_pct": 1.5,
        "min_examples": 30,
        "min_win_rate_pct": 45.0,
        "codegen_provider": "claude",
        "codegen_model": "claude-sonnet-4-5",
    }}
    mock_redis.return_value = _make_mock_redis(
        provider=b"claude", claude_model=b"claude-sonnet-4-5"
    )
    mock_collect.return_value = {"AAPL": []}
    mock_features.return_value = [{"bar_date": None}]

    from discoverer import CandidatePattern
    pattern = CandidatePattern("decision_tree", "rsi <= 30", 40, 2.0, 55.0, 0.8, "AAPL")
    mock_discover.return_value = [pattern]
    mock_codegen.return_value = "def generate_signal(s): return {'decision':'hold','confidence':0.5,'reasoning':'x'}"
    mock_save_strat.return_value = 1
    mock_save_run.return_value = None

    _run_phases()

    _, kwargs = mock_codegen.call_args
    assert kwargs.get("provider") == "claude"
    assert kwargs.get("model") == "claude-sonnet-4-5"


@patch("pipeline.save_ml_run")
@patch("pipeline.save_ml_strategy")
@patch("pipeline.ensure_ml_tables")
@patch("pipeline._send_discord_alert")
@patch("pipeline.generate_strategy_code")
@patch("pipeline.discover_patterns")
@patch("pipeline.compute_features")
@patch("pipeline.collect_bars")
@patch("pipeline.get_redis")
@patch("pipeline.load_config")
def test_pipeline_passes_gemini_settings_to_codegen(
    mock_cfg, mock_redis, mock_collect, mock_features,
    mock_discover, mock_codegen, mock_alert, mock_ensure,
    mock_save_strat, mock_save_run,
):
    from pipeline import _run_phases

    mock_cfg.return_value = {"ml": {
        "symbols": ["AAPL"],
        "lookback_days_momentum": 365,
        "lookback_days_regime": 1825,
        "max_strategies_per_run": 5,
        "min_forward_return_pct": 1.5,
        "min_examples": 30,
        "min_win_rate_pct": 45.0,
        "codegen_provider": "claude",
        "codegen_model": "claude-sonnet-4-5",
    }}
    mock_redis.return_value = _make_mock_redis(
        provider=b"gemini", gemini_model=b"gemini-2.0-flash"
    )
    mock_collect.return_value = {"AAPL": []}
    mock_features.return_value = [{"bar_date": None}]

    from discoverer import CandidatePattern
    pattern = CandidatePattern("decision_tree", "rsi <= 30", 40, 2.0, 55.0, 0.8, "AAPL")
    mock_discover.return_value = [pattern]
    mock_codegen.return_value = "def generate_signal(s): return {'decision':'hold','confidence':0.5,'reasoning':'x'}"
    mock_save_strat.return_value = 1
    mock_save_run.return_value = None

    _run_phases()

    _, kwargs = mock_codegen.call_args
    assert kwargs.get("provider") == "gemini"
    assert kwargs.get("model") == "gemini-2.0-flash"
