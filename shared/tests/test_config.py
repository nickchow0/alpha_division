import os
import pytest
from unittest.mock import patch

from shared.config import load_config


def test_load_config_reads_toml_file(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('log_level = "DEBUG"\nwatchlist = ["TSLA", "NVDA"]\n')
    with patch.dict(os.environ, {"CONFIG_FILE": str(cfg_file)}):
        cfg = load_config()
    assert cfg["log_level"] == "DEBUG"
    assert cfg["watchlist"] == ["TSLA", "NVDA"]


def test_load_config_returns_defaults_when_file_missing():
    with patch.dict(os.environ, {"CONFIG_FILE": "/nonexistent/config.toml"}):
        cfg = load_config()
    assert cfg["log_level"] == "INFO"
    assert cfg["watchlist"] == ["AAPL", "MSFT", "GOOGL"]


def test_load_config_merges_defaults_for_partial_file(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('log_level = "WARNING"\n')  # no watchlist key
    with patch.dict(os.environ, {"CONFIG_FILE": str(cfg_file)}):
        cfg = load_config()
    assert cfg["log_level"] == "WARNING"
    assert cfg["watchlist"] == ["AAPL", "MSFT", "GOOGL"]  # default preserved


def test_load_config_respects_config_file_env_var(tmp_path):
    cfg_file = tmp_path / "myconfig.toml"
    cfg_file.write_text('watchlist = ["AMZN"]\n')
    with patch.dict(os.environ, {"CONFIG_FILE": str(cfg_file)}):
        cfg = load_config()
    assert cfg["watchlist"] == ["AMZN"]


def test_load_config_does_not_mutate_defaults_on_repeated_calls(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('watchlist = ["AAPL"]\n')
    with patch.dict(os.environ, {"CONFIG_FILE": str(cfg_file)}):
        load_config()
    # Second call with missing file should still return original defaults
    with patch.dict(os.environ, {"CONFIG_FILE": "/nonexistent/config.toml"}):
        cfg = load_config()
    assert cfg["watchlist"] == ["AAPL", "MSFT", "GOOGL"]


def test_ml_config_defaults():
    """ML config keys exist in defaults even with empty config.toml."""
    with patch("shared.config.open", side_effect=FileNotFoundError):
        cfg = load_config()
    ml = cfg["ml"]
    assert isinstance(ml["symbols"], list)
    assert len(ml["symbols"]) == 26
    assert ml["lookback_days_momentum"] == 365
    assert ml["lookback_days_regime"] == 1825
    assert ml["max_strategies_per_run"] == 5
    assert ml["min_forward_return_pct"] == 1.5
    assert ml["min_examples"] == 30
    assert ml["min_win_rate_pct"] == 45.0
    assert ml["cron_schedule"] == "0 2 * * *"


def test_ml_config_partial_override_deep_merges(tmp_path):
    """A partial [ml] section in config.toml doesn't wipe other ML defaults."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_bytes(b"[ml]\nlookback_days_momentum = 730\n")
    with patch.dict(os.environ, {"CONFIG_FILE": str(toml_file)}):
        cfg = load_config()
    ml = cfg["ml"]
    assert ml["lookback_days_momentum"] == 730   # overridden
    assert ml["lookback_days_regime"] == 1825    # still from default
    assert len(ml["symbols"]) == 26              # still from default
