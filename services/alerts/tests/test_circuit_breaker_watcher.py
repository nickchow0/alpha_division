import unittest
from unittest.mock import patch, MagicMock
from contextlib import contextmanager
from datetime import date

from circuit_breaker_watcher import (
    is_cb_alerted_today,
    mark_cb_alerted,
    is_circuit_breaker_triggered,
    check_circuit_breaker,
)


def _make_mock_conn(rows):
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


@contextmanager
def _make_mock_cm(mock_conn):
    yield mock_conn


class TestIsCbAlertedToday(unittest.TestCase):
    @patch("circuit_breaker_watcher.get_redis")
    def test_returns_false_when_key_missing(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        result = is_cb_alerted_today(date(2026, 5, 15))
        self.assertFalse(result)
        mock_redis.get.assert_called_once_with("alert:cb_alerted:2026-05-15")

    @patch("circuit_breaker_watcher.get_redis")
    def test_returns_true_when_key_set(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.return_value = "1"
        mock_get_redis.return_value = mock_redis

        result = is_cb_alerted_today(date(2026, 5, 15))
        self.assertTrue(result)


class TestMarkCbAlerted(unittest.TestCase):
    @patch("circuit_breaker_watcher.get_redis")
    def test_sets_key_with_ttl(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        mark_cb_alerted(date(2026, 5, 15))
        mock_redis.setex.assert_called_once_with("alert:cb_alerted:2026-05-15", 86400, "1")


class TestIsCircuitBreakerTriggered(unittest.TestCase):
    @patch("circuit_breaker_watcher.get_conn")
    def test_returns_true_when_triggered(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn({"circuit_breaker_triggered": True})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = is_circuit_breaker_triggered(date(2026, 5, 15))
        self.assertTrue(result)

    @patch("circuit_breaker_watcher.get_conn")
    def test_returns_false_when_not_triggered(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn({"circuit_breaker_triggered": False})
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = is_circuit_breaker_triggered(date(2026, 5, 15))
        self.assertFalse(result)

    @patch("circuit_breaker_watcher.get_conn")
    def test_returns_false_when_no_row(self, mock_get_conn):
        mock_conn, mock_cur = _make_mock_conn(None)
        mock_get_conn.return_value = _make_mock_cm(mock_conn)

        result = is_circuit_breaker_triggered(date(2026, 5, 15))
        self.assertFalse(result)


class TestCheckCircuitBreaker(unittest.TestCase):
    @patch("circuit_breaker_watcher.mark_cb_alerted")
    @patch("circuit_breaker_watcher.is_circuit_breaker_triggered")
    @patch("circuit_breaker_watcher.is_cb_alerted_today")
    def test_no_alert_when_not_triggered(self, mock_alerted, mock_triggered, mock_mark):
        mock_alerted.return_value = False
        mock_triggered.return_value = False
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_circuit_breaker(
            today=date(2026, 5, 15),
            webhook_url="https://hook",
            send_discord_fn=mock_discord,
            send_email_fn=mock_email,
            email_from="f@x.com",
            email_to="t@x.com",
            sg_api_key="key",
        )

        mock_discord.assert_not_called()
        mock_email.assert_not_called()
        mock_mark.assert_not_called()

    @patch("circuit_breaker_watcher.mark_cb_alerted")
    @patch("circuit_breaker_watcher.is_circuit_breaker_triggered")
    @patch("circuit_breaker_watcher.is_cb_alerted_today")
    def test_no_alert_when_already_alerted(self, mock_alerted, mock_triggered, mock_mark):
        mock_alerted.return_value = True
        mock_triggered.return_value = True
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_circuit_breaker(
            today=date(2026, 5, 15),
            webhook_url="https://hook",
            send_discord_fn=mock_discord,
            send_email_fn=mock_email,
            email_from="f@x.com",
            email_to="t@x.com",
            sg_api_key="key",
        )

        mock_discord.assert_not_called()
        mock_email.assert_not_called()
        mock_mark.assert_not_called()

    @patch("circuit_breaker_watcher.mark_cb_alerted")
    @patch("circuit_breaker_watcher.is_circuit_breaker_triggered")
    @patch("circuit_breaker_watcher.is_cb_alerted_today")
    def test_sends_discord_and_email_when_triggered_and_not_alerted(
        self, mock_alerted, mock_triggered, mock_mark
    ):
        mock_alerted.return_value = False
        mock_triggered.return_value = True
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_circuit_breaker(
            today=date(2026, 5, 15),
            webhook_url="https://hook",
            send_discord_fn=mock_discord,
            send_email_fn=mock_email,
            email_from="f@x.com",
            email_to="t@x.com",
            sg_api_key="key",
        )

        mock_discord.assert_called_once()
        mock_email.assert_called_once()
        mock_mark.assert_called_once_with(date(2026, 5, 15))

    @patch("circuit_breaker_watcher.mark_cb_alerted")
    @patch("circuit_breaker_watcher.is_circuit_breaker_triggered")
    @patch("circuit_breaker_watcher.is_cb_alerted_today")
    def test_marks_alerted_after_sending(self, mock_alerted, mock_triggered, mock_mark):
        mock_alerted.return_value = False
        mock_triggered.return_value = True
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_circuit_breaker(
            today=date(2026, 5, 15),
            webhook_url="https://hook",
            send_discord_fn=mock_discord,
            send_email_fn=mock_email,
            email_from="f@x.com",
            email_to="t@x.com",
            sg_api_key="key",
        )

        mock_mark.assert_called_once_with(date(2026, 5, 15))

    @patch("circuit_breaker_watcher.mark_cb_alerted")
    @patch("circuit_breaker_watcher.is_circuit_breaker_triggered")
    @patch("circuit_breaker_watcher.is_cb_alerted_today")
    def test_discord_message_contains_circuit_breaker_text(
        self, mock_alerted, mock_triggered, mock_mark
    ):
        mock_alerted.return_value = False
        mock_triggered.return_value = True
        mock_discord = MagicMock()
        mock_email = MagicMock()

        check_circuit_breaker(
            today=date(2026, 5, 15),
            webhook_url="https://hook",
            send_discord_fn=mock_discord,
            send_email_fn=mock_email,
            email_from="f@x.com",
            email_to="t@x.com",
            sg_api_key="key",
        )

        msg = mock_discord.call_args[0][1]
        self.assertIn("circuit breaker", msg.lower())


if __name__ == "__main__":
    unittest.main()
