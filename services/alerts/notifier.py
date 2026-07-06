import requests
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


def send_discord(webhook_url: str, message: str) -> None:
    """POST a message to a Discord webhook. Raises on HTTP error."""
    resp = requests.post(webhook_url, json={"content": message}, timeout=10)
    resp.raise_for_status()


def send_email(
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    """Send a plain-text email via SendGrid. Raises on error."""
    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body,
    )
    sg = SendGridAPIClient(api_key)
    sg.send(message)
