import json
import pytest
from unittest.mock import patch, MagicMock

from publisher import publish_snapshot, publish_heartbeat


def _sample_snapshot() -> dict:
    """Helper to generate a sample snapshot with all expanded keys."""
    return {
        "symbol": "AAPL",
        "timestamp": "2026-05-15T14:00:00+00:00",
        "price": 175.50,
        # Legacy keys
        "rsi": 52.3, "sma20": 172.1, "sma50": 168.5,
        "sma20_prev": 171.8, "sma20_prev2": 171.5,
        # ML feature keys
        "rsi_7": 50.1, "rsi_14": 52.3, "rsi_21": 53.0,
        "mom_5d": 0.01, "mom_10d": 0.02, "mom_20d": 0.03,
        "sma_10": 174.0, "sma_20": 172.1, "sma_50": 168.5, "sma_200": 160.0,
        "dist_sma10": 0.009, "dist_sma20": 0.02, "dist_sma50": 0.04, "dist_sma200": 0.097,
        "atr_14": 3.1, "bb_width": 0.06, "dist_bb_upper": 0.02, "dist_bb_lower": 0.04,
        "vol_zscore": 0.5, "vol_ratio": 1.1,
        "macd_line": 0.8, "macd_signal": 0.6, "macd_hist": 0.2,
        "dist_52w_high": 0.05, "dist_52w_low": 0.2,
        "day_of_week": 2, "volume": 1_500_000,
        "news": [{"headline": "Apple hits record", "datetime": 1715000000}],
        "macro": {"fed_funds_rate": 5.33, "cpi": 314.5},
    }


# ---------------------------------------------------------------------------
# publish_snapshot() tests
# ---------------------------------------------------------------------------

def test_publish_snapshot_calls_xadd():
    """Verify that publish_snapshot calls redis.xadd()."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        mock_redis.xadd.assert_called_once()


def test_publish_snapshot_uses_correct_stream_key():
    """Verify that xadd is called with the correct stream key."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        # First positional arg should be the stream key
        args, kwargs = mock_redis.xadd.call_args
        assert args[0] == "stream:market_snapshot"


def test_publish_snapshot_sends_data_field():
    """Verify that the fields dict contains a 'data' key."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        args, kwargs = mock_redis.xadd.call_args
        # Second positional arg should be the fields dict
        fields = args[1]
        assert "data" in fields


def test_publish_snapshot_data_field_is_valid_json():
    """Verify that the 'data' field contains valid JSON."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        args, kwargs = mock_redis.xadd.call_args
        fields = args[1]
        # Should be able to parse JSON without error
        parsed = json.loads(fields["data"])
        assert isinstance(parsed, dict)


def test_publish_snapshot_json_contains_all_required_fields():
    """Verify that the JSON contains all legacy and ML feature fields."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        args, kwargs = mock_redis.xadd.call_args
        fields = args[1]
        parsed = json.loads(fields["data"])

        required_fields = [
            "symbol", "timestamp", "price",
            "rsi", "sma20", "sma50", "sma20_prev", "sma20_prev2",
            "rsi_7", "rsi_14", "rsi_21",
            "macd_hist", "atr_14", "vol_zscore", "vol_ratio",
            "dist_sma20", "dist_52w_high", "day_of_week", "volume",
            "news", "macro",
        ]
        for field in required_fields:
            assert field in parsed, f"Missing required field: {field}"


def test_publish_snapshot_caps_stream_at_1000():
    """Verify that xadd is called with maxlen=1000."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        args, kwargs = mock_redis.xadd.call_args
        # maxlen should be passed as a keyword argument
        assert "maxlen" in kwargs
        assert kwargs["maxlen"] == 1000


def test_publish_snapshot_caches_latest_per_symbol():
    """Verify publish_snapshot writes the snapshot to snapshot:<symbol> Redis key."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        mock_redis.set.assert_called_once()
        key = mock_redis.set.call_args[0][0]
        assert key == "snapshot:AAPL"


def test_publish_snapshot_cache_value_is_valid_json():
    """Verify the cached snapshot value is valid JSON containing the full snapshot."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        snapshot = _sample_snapshot()
        publish_snapshot(snapshot)
        value = mock_redis.set.call_args[0][1]
        parsed = json.loads(value)
        assert parsed["symbol"] == "AAPL"
        assert parsed["price"] == 175.50


# ---------------------------------------------------------------------------
# publish_heartbeat() tests
# ---------------------------------------------------------------------------

def test_publish_heartbeat_calls_setex():
    """Verify that publish_heartbeat calls redis.setex()."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        publish_heartbeat()
        mock_redis.setex.assert_called_once()


def test_publish_heartbeat_uses_correct_key():
    """Verify that setex is called with the correct key."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        publish_heartbeat()
        args, kwargs = mock_redis.setex.call_args
        # First positional arg should be the heartbeat key
        assert args[0] == "heartbeat:data"


def test_publish_heartbeat_ttl_is_90():
    """Verify that setex is called with TTL of 90 seconds."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        publish_heartbeat()
        args, kwargs = mock_redis.setex.call_args
        # Second positional arg should be the TTL
        assert args[1] == 90


def test_publish_heartbeat_value_is_ok():
    """Verify that setex is called with value 'ok'."""
    mock_redis = MagicMock()
    with patch("publisher.get_redis", return_value=mock_redis):
        publish_heartbeat()
        args, kwargs = mock_redis.setex.call_args
        # Third positional arg should be the value
        assert args[2] == "ok"
