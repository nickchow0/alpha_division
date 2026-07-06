import unittest
from unittest.mock import patch, MagicMock

from service_status import get_service_statuses, MONITORED_SERVICES


class TestGetServiceStatuses(unittest.TestCase):
    @patch("service_status.get_redis")
    def test_returns_entry_per_service(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 60
        mock_get_redis.return_value = mock_redis

        result = get_service_statuses()
        self.assertEqual(len(result), len(MONITORED_SERVICES))

    @patch("service_status.get_redis")
    def test_alive_when_ttl_positive(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 45
        mock_get_redis.return_value = mock_redis

        result = get_service_statuses()
        for entry in result:
            self.assertTrue(entry["alive"])

    @patch("service_status.get_redis")
    def test_dead_when_ttl_minus_two(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = -2
        mock_get_redis.return_value = mock_redis

        result = get_service_statuses()
        for entry in result:
            self.assertFalse(entry["alive"])

    @patch("service_status.get_redis")
    def test_dead_when_ttl_minus_one(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = -1
        mock_get_redis.return_value = mock_redis

        result = get_service_statuses()
        for entry in result:
            self.assertFalse(entry["alive"])

    @patch("service_status.get_redis")
    def test_entry_has_name_alive_ttl_fields(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 70
        mock_get_redis.return_value = mock_redis

        result = get_service_statuses()
        for entry in result:
            self.assertIn("name", entry)
            self.assertIn("alive", entry)
            self.assertIn("ttl", entry)

    @patch("service_status.get_redis")
    def test_checks_correct_heartbeat_keys(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 60
        mock_get_redis.return_value = mock_redis

        get_service_statuses()

        called_keys = [call[0][0] for call in mock_redis.ttl.call_args_list]
        for service in MONITORED_SERVICES:
            self.assertIn(f"heartbeat:{service}", called_keys)

    @patch("service_status.get_redis")
    def test_service_names_match_monitored_services(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.ttl.return_value = 60
        mock_get_redis.return_value = mock_redis

        result = get_service_statuses()
        names = [entry["name"] for entry in result]
        self.assertEqual(names, MONITORED_SERVICES)


if __name__ == "__main__":
    unittest.main()
