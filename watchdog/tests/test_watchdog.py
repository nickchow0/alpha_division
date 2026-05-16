"""Tests for watchdog.py."""
import subprocess
import unittest
from unittest.mock import MagicMock, patch, call

import requests


class TestSendDiscord(unittest.TestCase):
    @patch("watchdog.watchdog.requests.post")
    def test_sends_post_to_webhook(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status = MagicMock()
        from watchdog.watchdog import send_discord
        send_discord("https://discord.test/webhook", "hello")
        mock_post.assert_called_once_with(
            "https://discord.test/webhook",
            json={"content": "hello"},
            timeout=10,
        )

    @patch("watchdog.watchdog.requests.post")
    def test_raises_on_http_error(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = Exception("bad")
        from watchdog.watchdog import send_discord
        with self.assertRaises(Exception):
            send_discord("https://discord.test/webhook", "hello")


class TestSendEmail(unittest.TestCase):
    @patch("watchdog.watchdog.SendGridAPIClient")
    @patch("watchdog.watchdog.Mail")
    def test_sends_email_via_sendgrid(self, mock_mail_cls, mock_sg_cls):
        mock_sg = MagicMock()
        mock_sg_cls.return_value = mock_sg
        mock_mail = MagicMock()
        mock_mail_cls.return_value = mock_mail

        from watchdog.watchdog import send_email
        send_email("key", "from@x.com", "to@x.com", "Subject", "Body")

        mock_mail_cls.assert_called_once_with(
            from_email="from@x.com",
            to_emails="to@x.com",
            subject="Subject",
            plain_text_content="Body",
        )
        mock_sg.send.assert_called_once_with(mock_mail)


class TestRedisHelpers(unittest.TestCase):
    def _make_redis(self):
        return MagicMock()

    def test_get_heartbeat_ttl_returns_positive_when_alive(self):
        from watchdog.watchdog import get_heartbeat_ttl
        r = self._make_redis()
        r.ttl.return_value = 45
        result = get_heartbeat_ttl(r, "data")
        r.ttl.assert_called_once_with("heartbeat:data")
        self.assertEqual(result, 45)

    def test_get_heartbeat_ttl_returns_negative_when_missing(self):
        from watchdog.watchdog import get_heartbeat_ttl
        r = self._make_redis()
        r.ttl.return_value = -2  # key does not exist
        result = get_heartbeat_ttl(r, "data")
        self.assertEqual(result, -2)

    def test_get_alert_state_returns_none_when_missing(self):
        from watchdog.watchdog import get_alert_state
        r = self._make_redis()
        r.get.return_value = None
        result = get_alert_state(r, "data")
        r.get.assert_called_once_with("watchdog:alerted:data")
        self.assertIsNone(result)

    def test_get_alert_state_returns_decoded_string(self):
        from watchdog.watchdog import get_alert_state
        r = self._make_redis()
        r.get.return_value = b"alerted"
        result = get_alert_state(r, "data")
        self.assertEqual(result, "alerted")

    def test_set_alert_state_uses_setex(self):
        from watchdog.watchdog import set_alert_state
        r = self._make_redis()
        set_alert_state(r, "data", "critical")
        r.setex.assert_called_once_with("watchdog:alerted:data", 3600, "critical")

    def test_clear_service_state_deletes_both_keys(self):
        from watchdog.watchdog import clear_service_state
        r = self._make_redis()
        clear_service_state(r, "data")
        r.delete.assert_any_call("watchdog:alerted:data")
        r.delete.assert_any_call("watchdog:restarts:data")
        self.assertEqual(r.delete.call_count, 2)

    def test_get_restart_count_returns_zero_when_missing(self):
        from watchdog.watchdog import get_restart_count
        r = self._make_redis()
        r.get.return_value = None
        result = get_restart_count(r, "data")
        self.assertEqual(result, 0)

    def test_get_restart_count_returns_int(self):
        from watchdog.watchdog import get_restart_count
        r = self._make_redis()
        r.get.return_value = b"2"
        result = get_restart_count(r, "data")
        self.assertEqual(result, 2)

    def test_increment_restart_count_sets_expire_on_first_call(self):
        from watchdog.watchdog import increment_restart_count
        r = self._make_redis()
        r.incr.return_value = 1  # first increment
        result = increment_restart_count(r, "data")
        r.incr.assert_called_once_with("watchdog:restarts:data")
        r.expire.assert_called_once_with("watchdog:restarts:data", 3600)
        self.assertEqual(result, 1)

    def test_increment_restart_count_skips_expire_on_subsequent_calls(self):
        from watchdog.watchdog import increment_restart_count
        r = self._make_redis()
        r.incr.return_value = 2  # not the first increment
        result = increment_restart_count(r, "data")
        r.expire.assert_not_called()
        self.assertEqual(result, 2)


class TestRestartService(unittest.TestCase):
    @patch("watchdog.watchdog.subprocess.run")
    def test_returns_true_on_success(self, mock_run):
        mock_run.return_value.returncode = 0
        from watchdog.watchdog import restart_service
        result = restart_service("data")
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["docker", "compose", "restart", "data"],
            cwd=unittest.mock.ANY,
            capture_output=True,
            text=True,
            timeout=60,
        )

    @patch("watchdog.watchdog.subprocess.run")
    def test_returns_false_on_nonzero_exit(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "error"
        from watchdog.watchdog import restart_service
        result = restart_service("data")
        self.assertFalse(result)

    @patch("watchdog.watchdog.subprocess.run")
    def test_returns_false_on_exception(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["docker"], 60)
        from watchdog.watchdog import restart_service
        result = restart_service("data")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
