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


class TestCheckDashboardHealth(unittest.TestCase):
    @patch("watchdog.watchdog.requests.get")
    @patch("watchdog.watchdog.send_discord")
    def test_no_alert_when_healthy(self, mock_discord, mock_get):
        mock_get.return_value.status_code = 200
        from watchdog.watchdog import check_dashboard_health
        check_dashboard_health("https://discord.test/webhook")
        mock_discord.assert_not_called()

    @patch("watchdog.watchdog.requests.get")
    @patch("watchdog.watchdog.send_discord")
    def test_sends_discord_on_non_200(self, mock_discord, mock_get):
        mock_get.return_value.status_code = 500
        from watchdog.watchdog import check_dashboard_health
        check_dashboard_health("https://discord.test/webhook")
        mock_discord.assert_called_once()
        msg = mock_discord.call_args[0][1]
        self.assertIn("dashboard", msg.lower())

    @patch("watchdog.watchdog.requests.get")
    @patch("watchdog.watchdog.send_discord")
    def test_sends_discord_on_exception(self, mock_discord, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")
        from watchdog.watchdog import check_dashboard_health
        check_dashboard_health("https://discord.test/webhook")
        mock_discord.assert_called_once()

    @patch("watchdog.watchdog.requests.get")
    @patch("watchdog.watchdog.send_discord")
    def test_swallows_discord_failure_gracefully(self, mock_discord, mock_get):
        mock_get.return_value.status_code = 500
        mock_discord.side_effect = Exception("discord down")
        from watchdog.watchdog import check_dashboard_health
        # Should not raise
        check_dashboard_health("https://discord.test/webhook")


class TestCheckService(unittest.TestCase):
    def _cfg(self):
        return {
            "webhook_url": "https://discord.test/webhook",
            "sg_api_key": "SG.test",
            "email_from": "from@x.com",
            "email_to": "to@x.com",
        }

    def _make_redis(self, ttl=45, alert_state=None, restart_count=0):
        r = MagicMock()
        r.ttl.return_value = ttl
        r.get.side_effect = lambda key: (
            alert_state.encode() if alert_state and "alerted" in key else
            str(restart_count).encode() if restart_count and "restarts" in key else
            None
        )
        r.incr.return_value = restart_count + 1
        return r

    @patch("watchdog.watchdog.clear_service_state")
    @patch("watchdog.watchdog.send_discord")
    def test_branch1_up_previously_alerted_sends_recovery(self, mock_disc, mock_clear):
        """Branch 1: UP + alerted → recovery Discord + clear state."""
        r = self._make_redis(ttl=45, alert_state="alerted")
        from watchdog.watchdog import check_service
        check_service(r, "data", self._cfg())
        mock_disc.assert_called_once()
        msg = mock_disc.call_args[0][1]
        self.assertIn("recovered", msg.lower())
        mock_clear.assert_called_once_with(r, "data")

    @patch("watchdog.watchdog.send_discord")
    def test_branch2_up_not_alerted_is_noop(self, mock_disc):
        """Branch 2: UP + not alerted → no-op."""
        r = self._make_redis(ttl=45, alert_state=None)
        from watchdog.watchdog import check_service
        check_service(r, "data", self._cfg())
        mock_disc.assert_not_called()

    @patch("watchdog.watchdog.set_alert_state")
    @patch("watchdog.watchdog.send_email")
    @patch("watchdog.watchdog.send_discord")
    def test_branch3_down_max_restarts_not_critical_sends_critical(
        self, mock_disc, mock_email, mock_set_state
    ):
        """Branch 3: DOWN + count >= 3 + not critical → CRITICAL alert."""
        r = self._make_redis(ttl=-2, alert_state="alerted", restart_count=3)
        from watchdog.watchdog import check_service
        check_service(r, "data", self._cfg())
        mock_disc.assert_called_once()
        msg = mock_disc.call_args[0][1]
        self.assertIn("critical", msg.lower())
        mock_email.assert_called_once()
        mock_set_state.assert_called_once_with(r, "data", "critical")

    @patch("watchdog.watchdog.send_discord")
    def test_branch4_down_max_restarts_already_critical_is_noop(self, mock_disc):
        """Branch 4: DOWN + count >= 3 + already critical → no-op."""
        r = self._make_redis(ttl=-2, alert_state="critical", restart_count=3)
        from watchdog.watchdog import check_service
        check_service(r, "data", self._cfg())
        mock_disc.assert_not_called()

    @patch("watchdog.watchdog.increment_restart_count")
    @patch("watchdog.watchdog.restart_service")
    @patch("watchdog.watchdog.set_alert_state")
    @patch("watchdog.watchdog.send_email")
    @patch("watchdog.watchdog.send_discord")
    def test_branch5_down_first_failure_alerts_and_restarts(
        self, mock_disc, mock_email, mock_set_state, mock_restart, mock_incr
    ):
        """Branch 5: DOWN + count < 3 + not alerted → alert + restart."""
        r = self._make_redis(ttl=-2, alert_state=None, restart_count=0)
        from watchdog.watchdog import check_service
        check_service(r, "data", self._cfg())
        mock_disc.assert_called_once()
        msg = mock_disc.call_args[0][1]
        self.assertNotIn("critical", msg.lower())
        mock_email.assert_called_once()
        mock_set_state.assert_called_once_with(r, "data", "alerted")
        mock_restart.assert_called_once_with("data")
        mock_incr.assert_called_once_with(r, "data")

    @patch("watchdog.watchdog.increment_restart_count")
    @patch("watchdog.watchdog.restart_service")
    @patch("watchdog.watchdog.send_email")
    @patch("watchdog.watchdog.send_discord")
    def test_branch6_down_already_alerted_restarts_without_duplicate_alert(
        self, mock_disc, mock_email, mock_restart, mock_incr
    ):
        """Branch 6: DOWN + count < 3 + already alerted → restart only."""
        r = self._make_redis(ttl=-2, alert_state="alerted", restart_count=1)
        from watchdog.watchdog import check_service
        check_service(r, "data", self._cfg())
        mock_disc.assert_not_called()
        mock_email.assert_not_called()
        mock_restart.assert_called_once_with("data")
        mock_incr.assert_called_once_with(r, "data")

    @patch("watchdog.watchdog.send_discord")
    def test_notification_errors_are_swallowed(self, mock_disc):
        """Discord failures should not propagate — watchdog must keep running."""
        mock_disc.side_effect = Exception("discord down")
        r = self._make_redis(ttl=45, alert_state="alerted")
        from watchdog.watchdog import check_service
        # Should not raise
        check_service(r, "data", self._cfg())


class TestRunCycle(unittest.TestCase):
    def _cfg(self):
        return {
            "webhook_url": "https://discord.test/webhook",
            "sg_api_key": "SG.test",
            "email_from": "from@x.com",
            "email_to": "to@x.com",
        }

    @patch("watchdog.watchdog.check_dashboard_health")
    @patch("watchdog.watchdog.check_service")
    def test_calls_check_service_for_all_services(self, mock_check, mock_dash):
        from watchdog.watchdog import run_cycle, SERVICES
        r = MagicMock()
        cfg = self._cfg()
        run_cycle(r, cfg)
        self.assertEqual(mock_check.call_count, len(SERVICES))
        for svc in SERVICES:
            mock_check.assert_any_call(r, svc, cfg)

    @patch("watchdog.watchdog.check_dashboard_health")
    @patch("watchdog.watchdog.check_service")
    def test_calls_check_dashboard_health(self, mock_check, mock_dash):
        from watchdog.watchdog import run_cycle
        r = MagicMock()
        run_cycle(r, self._cfg())
        mock_dash.assert_called_once_with(self._cfg()["webhook_url"])

    @patch("watchdog.watchdog.check_dashboard_health")
    @patch("watchdog.watchdog.check_service")
    def test_continues_after_check_service_exception(self, mock_check, mock_dash):
        """A single service exception must not abort the cycle."""
        mock_check.side_effect = [Exception("boom"), None, None, None, None]
        from watchdog.watchdog import run_cycle, SERVICES
        r = MagicMock()
        # Should not raise
        run_cycle(r, self._cfg())
        # All services still attempted
        self.assertEqual(mock_check.call_count, len(SERVICES))


if __name__ == "__main__":
    unittest.main()
