import unittest
from unittest.mock import patch, MagicMock

from notifier import send_discord, send_email


class TestSendDiscord(unittest.TestCase):
    @patch("notifier.requests.post")
    def test_sends_post_to_webhook(self, mock_post):
        mock_post.return_value.raise_for_status = MagicMock()
        send_discord("https://discord.example/webhook", "hello")
        mock_post.assert_called_once_with(
            "https://discord.example/webhook",
            json={"content": "hello"},
            timeout=10,
        )

    @patch("notifier.requests.post")
    def test_raises_on_http_error(self, mock_post):
        mock_post.return_value.raise_for_status.side_effect = Exception("HTTP 400")
        with self.assertRaises(Exception):
            send_discord("https://discord.example/webhook", "hello")


class TestSendEmail(unittest.TestCase):
    @patch("notifier.SendGridAPIClient")
    @patch("notifier.Mail")
    def test_sends_email(self, mock_mail_cls, mock_sg_cls):
        mock_sg = MagicMock()
        mock_sg_cls.return_value = mock_sg

        send_email(
            api_key="key",
            from_email="from@example.com",
            to_email="to@example.com",
            subject="Subject",
            body="Body text",
        )

        mock_mail_cls.assert_called_once_with(
            from_email="from@example.com",
            to_emails="to@example.com",
            subject="Subject",
            plain_text_content="Body text",
        )
        mock_sg.send.assert_called_once()

    @patch("notifier.SendGridAPIClient")
    @patch("notifier.Mail")
    def test_raises_on_sendgrid_error(self, mock_mail_cls, mock_sg_cls):
        mock_sg = MagicMock()
        mock_sg.send.side_effect = Exception("SendGrid error")
        mock_sg_cls.return_value = mock_sg
        with self.assertRaises(Exception):
            send_email("key", "from@x.com", "to@x.com", "sub", "body")


if __name__ == "__main__":
    unittest.main()
