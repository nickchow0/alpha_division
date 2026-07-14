"""Unit tests for pipeline.py's heartbeat publishing — all Redis I/O is mocked."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from unittest.mock import MagicMock, patch


@patch("pipeline.get_redis")
def test_publish_heartbeat_sets_correct_key_and_ttl(mock_get_redis):
    from pipeline import _publish_heartbeat, _HEARTBEAT_KEY, _HEARTBEAT_TTL

    mock_redis = MagicMock()
    mock_get_redis.return_value = mock_redis

    _publish_heartbeat()

    mock_redis.setex.assert_called_once_with(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")
