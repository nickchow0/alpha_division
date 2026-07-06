import pytest
from unittest.mock import patch, MagicMock

from alerter import send_alert


def test_send_alert_posts_to_webhook_when_url_is_set():
    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example.com/webhook/123"}):
        with patch("alerter.requests.post", return_value=mock_response) as mock_post:
            send_alert("Claude call failed for AAPL")

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "https://discord.example.com/webhook/123"
    assert "Claude call failed for AAPL" in str(call_kwargs[1].get("json", {}))


def test_send_alert_payload_contains_message():
    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example.com/webhook/123"}):
        with patch("alerter.requests.post", return_value=mock_response) as mock_post:
            send_alert("DB write failed for TSLA")

    payload = mock_post.call_args[1]["json"]
    assert payload == {"content": "DB write failed for TSLA"}


def test_send_alert_falls_back_to_warning_when_url_absent():
    env_without_webhook = {k: v for k, v in __import__("os").environ.items() if k != "DISCORD_WEBHOOK_URL"}

    with patch.dict("os.environ", env_without_webhook, clear=True):
        with patch("alerter.requests.post") as mock_post:
            with patch("alerter.log") as mock_log:
                send_alert("stream read failed")

    mock_post.assert_not_called()
    mock_log.warning.assert_called_once()
    warning_msg = mock_log.warning.call_args[0][0]
    assert "stream read failed" in warning_msg


def test_send_alert_does_not_crash_on_network_error():
    with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example.com/webhook/123"}):
        with patch("alerter.requests.post", side_effect=Exception("Connection refused")):
            send_alert("something broke")  # must not raise


def test_send_alert_does_not_crash_on_http_error():
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = Exception("500 Server Error")

    with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example.com/webhook/123"}):
        with patch("alerter.requests.post", return_value=mock_response):
            send_alert("something broke")  # must not raise


def test_send_alert_logs_error_on_failed_post():
    with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.example.com/webhook/123"}):
        with patch("alerter.requests.post", side_effect=Exception("Connection refused")):
            with patch("alerter.log") as mock_log:
                send_alert("something broke")

    mock_log.error.assert_called_once()
    assert "Connection refused" in mock_log.error.call_args[0][0]
