import unittest
from unittest.mock import patch, MagicMock

from crash_watcher import (
    is_service_alive,
    is_crash_suspected,
    is_crash_alerted,
    mark_crash_suspected,
    mark_crash_alerted,
    clear_crash_state,
    check_service,
)

SERVICES = ["data", "analysis", "execution"]


class TestIsServiceAlive(unittest.TestCase):
    @patch("crash_watcher.get_redis")
    def test_alive_when_heartbeat_key_exists(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        mock_get_redis.return_value = mock_redis

        self.assertTrue(is_service_alive("data"))
        mock_redis.exists.assert_called_once_with("heartbeat:data")

    @patch("crash_watcher.get_redis")
    def test_dead_when_heartbeat_key_missing(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0
        mock_get_redis.return_value = mock_redis

        self.assertFalse(is_service_alive("data"))


class TestIsCrashSuspected(unittest.TestCase):
    @patch("crash_watcher.get_redis")
    def test_returns_true_when_suspect_key_exists(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        mock_get_redis.return_value = mock_redis

        self.assertTrue(is_crash_suspected("data"))
        mock_redis.exists.assert_called_once_with("alert:crash_suspect:data")

    @patch("crash_watcher.get_redis")
    def test_returns_false_when_suspect_key_missing(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0
        mock_get_redis.return_value = mock_redis

        self.assertFalse(is_crash_suspected("data"))


class TestIsCrashAlerted(unittest.TestCase):
    @patch("crash_watcher.get_redis")
    def test_returns_true_when_crash_key_exists(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        mock_get_redis.return_value = mock_redis

        self.assertTrue(is_crash_alerted("data"))
        mock_redis.exists.assert_called_once_with("alert:crash:data")

    @patch("crash_watcher.get_redis")
    def test_returns_false_when_crash_key_missing(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0
        mock_get_redis.return_value = mock_redis

        self.assertFalse(is_crash_alerted("data"))


class TestMarkCrashSuspected(unittest.TestCase):
    @patch("crash_watcher.get_redis")
    def test_sets_suspect_key_with_ttl(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        mark_crash_suspected("analysis")
        mock_redis.setex.assert_called_once_with("alert:crash_suspect:analysis", 90, "1")


class TestMarkCrashAlerted(unittest.TestCase):
    @patch("crash_watcher.get_redis")
    def test_sets_crash_key_with_ttl(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        mark_crash_alerted("execution")
        mock_redis.setex.assert_called_once_with("alert:crash:execution", 3600, "1")


class TestClearCrashState(unittest.TestCase):
    @patch("crash_watcher.get_redis")
    def test_deletes_both_keys(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        clear_crash_state("data")
        mock_redis.delete.assert_any_call("alert:crash:data")
        mock_redis.delete.assert_any_call("alert:crash_suspect:data")
        self.assertEqual(mock_redis.delete.call_count, 2)


class TestCheckService(unittest.TestCase):
    @patch("crash_watcher.clear_crash_state")
    @patch("crash_watcher.mark_crash_alerted")
    @patch("crash_watcher.mark_crash_suspected")
    @patch("crash_watcher.is_crash_alerted")
    @patch("crash_watcher.is_crash_suspected")
    @patch("crash_watcher.is_service_alive")
    def test_alive_no_prior_alert_does_nothing(
        self, mock_alive, mock_suspected, mock_alerted, mock_mark_suspected,
        mock_mark_alerted, mock_clear
    ):
        mock_alive.return_value = True
        mock_alerted.return_value = False
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_service("data", "https://hook", mock_discord, mock_email, "f@x", "t@x", "key")

        mock_discord.assert_not_called()
        mock_email.assert_not_called()
        mock_mark_suspected.assert_not_called()
        mock_mark_alerted.assert_not_called()

    @patch("crash_watcher.clear_crash_state")
    @patch("crash_watcher.mark_crash_alerted")
    @patch("crash_watcher.mark_crash_suspected")
    @patch("crash_watcher.is_crash_alerted")
    @patch("crash_watcher.is_crash_suspected")
    @patch("crash_watcher.is_service_alive")
    def test_alive_after_crash_sends_recovery_and_clears(
        self, mock_alive, mock_suspected, mock_alerted, mock_mark_suspected,
        mock_mark_alerted, mock_clear
    ):
        mock_alive.return_value = True
        mock_alerted.return_value = True
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_service("data", "https://hook", mock_discord, mock_email, "f@x", "t@x", "key")

        mock_discord.assert_called_once()
        msg = mock_discord.call_args[0][1]
        self.assertIn("recover", msg.lower())
        mock_clear.assert_called_once_with("data")

    @patch("crash_watcher.clear_crash_state")
    @patch("crash_watcher.mark_crash_alerted")
    @patch("crash_watcher.mark_crash_suspected")
    @patch("crash_watcher.is_crash_alerted")
    @patch("crash_watcher.is_crash_suspected")
    @patch("crash_watcher.is_service_alive")
    def test_first_miss_marks_suspected(
        self, mock_alive, mock_suspected, mock_alerted, mock_mark_suspected,
        mock_mark_alerted, mock_clear
    ):
        mock_alive.return_value = False
        mock_alerted.return_value = False
        mock_suspected.return_value = False
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_service("data", "https://hook", mock_discord, mock_email, "f@x", "t@x", "key")

        mock_mark_suspected.assert_called_once_with("data")
        mock_discord.assert_not_called()
        mock_email.assert_not_called()

    @patch("crash_watcher.clear_crash_state")
    @patch("crash_watcher.mark_crash_alerted")
    @patch("crash_watcher.mark_crash_suspected")
    @patch("crash_watcher.is_crash_alerted")
    @patch("crash_watcher.is_crash_suspected")
    @patch("crash_watcher.is_service_alive")
    def test_second_miss_sends_discord_and_email(
        self, mock_alive, mock_suspected, mock_alerted, mock_mark_suspected,
        mock_mark_alerted, mock_clear
    ):
        mock_alive.return_value = False
        mock_alerted.return_value = False
        mock_suspected.return_value = True
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_service("data", "https://hook", mock_discord, mock_email, "f@x", "t@x", "key")

        mock_discord.assert_called_once()
        mock_email.assert_called_once()
        mock_mark_alerted.assert_called_once_with("data")

    @patch("crash_watcher.clear_crash_state")
    @patch("crash_watcher.mark_crash_alerted")
    @patch("crash_watcher.mark_crash_suspected")
    @patch("crash_watcher.is_crash_alerted")
    @patch("crash_watcher.is_crash_suspected")
    @patch("crash_watcher.is_service_alive")
    def test_dead_already_alerted_does_nothing(
        self, mock_alive, mock_suspected, mock_alerted, mock_mark_suspected,
        mock_mark_alerted, mock_clear
    ):
        mock_alive.return_value = False
        mock_alerted.return_value = True
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_service("data", "https://hook", mock_discord, mock_email, "f@x", "t@x", "key")

        mock_discord.assert_not_called()
        mock_email.assert_not_called()
        mock_mark_suspected.assert_not_called()
        mock_mark_alerted.assert_not_called()

    @patch("crash_watcher.clear_crash_state")
    @patch("crash_watcher.mark_crash_alerted")
    @patch("crash_watcher.mark_crash_suspected")
    @patch("crash_watcher.is_crash_alerted")
    @patch("crash_watcher.is_crash_suspected")
    @patch("crash_watcher.is_service_alive")
    def test_crash_message_contains_service_name(
        self, mock_alive, mock_suspected, mock_alerted, mock_mark_suspected,
        mock_mark_alerted, mock_clear
    ):
        mock_alive.return_value = False
        mock_alerted.return_value = False
        mock_suspected.return_value = True
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_service("analysis", "https://hook", mock_discord, mock_email, "f@x", "t@x", "key")

        msg = mock_discord.call_args[0][1]
        self.assertIn("analysis", msg.lower())


if __name__ == "__main__":
    unittest.main()
