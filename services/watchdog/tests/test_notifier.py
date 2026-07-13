import os
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token")

from notifier import send_notification


def test_send_notification_posts_to_discord():
    with patch("notifier.requests.post") as mock_post:
        mock_post.return_value.status_code = 204
        send_notification("test message")
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "https://discord.com/api/webhooks/test/token"
    assert call_kwargs[1]["json"]["content"] == "test message"


def test_send_notification_no_webhook_url_does_not_raise():
    with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": ""}):
        # Should log and return, not raise
        send_notification("test")  # no error


def test_send_notification_http_error_does_not_raise():
    with patch("notifier.requests.post", side_effect=Exception("network error")):
        send_notification("test")  # should not raise
