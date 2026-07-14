import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import patch, MagicMock


class TestPublishHeartbeat(unittest.TestCase):
    @patch("main.get_redis")
    def test_publish_heartbeat_sets_correct_key_and_ttl(self, mock_get_redis):
        from main import _publish_heartbeat, _HEARTBEAT_KEY, _HEARTBEAT_TTL

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        _publish_heartbeat()

        mock_redis.setex.assert_called_once_with(_HEARTBEAT_KEY, _HEARTBEAT_TTL, "ok")


if __name__ == "__main__":
    unittest.main()
