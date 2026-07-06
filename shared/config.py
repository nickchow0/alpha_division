import os
import time
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]  # pip install tomli

_DEFAULT_CONFIG: dict = {
    "log_level": "INFO",
    "watchlist": ["AAPL", "MSFT", "GOOGL"],
    "paper_balance": 100000.0,
    "analysis": {
        "ai_provider": "claude",
        "claude_model": "claude-haiku-4-5",
        "gemini_model": "gemini-2.5-flash",
    },
    "execution": {
        "max_positions": 10,
        "max_short_positions": 5,
        "position_size_pct": 0.04,
    },
    "ml": {
        "symbols": [
            "CRWD", "SNOW", "DDOG", "SHOP", "MELI", "COIN", "UBER", "AXON",
            "PLTR", "AI", "BBAI", "SOUN", "IONQ", "RXRX", "GTLB", "PATH",
            "S", "CPNG", "MRVL", "MPWR", "WOLF", "SITM", "ONTO", "ALAB",
            "SMCI", "SNDK",
        ],
        "lookback_days_momentum": 365,
        "lookback_days_regime": 1825,
        "max_strategies_per_run": 5,
        "min_forward_return_pct": 1.5,
        "min_examples": 30,
        "min_win_rate_pct": 45.0,
        "cron_schedule": "0 2 * * *",
        "min_replay_signal_rate": 0.20,
        "min_replay_buy_rate": 0.40,
    },
}


def load_config() -> dict:
    """Load configuration from config.toml.

    CONFIG_FILE env var overrides the default path (/app/config.toml).
    Falls back to built-in defaults if the file is not found.
    Secrets (API keys, passwords) must NOT be placed here — use .env instead.
    """
    path = Path(os.environ.get("CONFIG_FILE", "/app/config.toml"))
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
            result = dict(_DEFAULT_CONFIG)
            for key, val in data.items():
                if isinstance(val, dict) and isinstance(result.get(key), dict):
                    result[key] = {**result[key], **val}
                else:
                    result[key] = val
            return result
        except FileNotFoundError:
            return dict(_DEFAULT_CONFIG)
        except tomllib.TOMLDecodeError as exc:
            last_exc = exc
            time.sleep(0.1)
    raise RuntimeError(f"Failed to parse {path} after 3 attempts: {last_exc}")
