
import base64
import sys
from email.message import EmailMessage

import django
import requests
from django.utils import timezone

# --- correct import: MCPIntegration lives in agent/models.py, not apps.integrations ---
from agent.models import MCPIntegration


def get_valid_integration(user_id: int, service: str = "gmail") -> MCPIntegration:
    """Fetch the integration and fail loudly (with a clear reason) instead of
    silently trying an expired or missing token."""
    try:
        integration = MCPIntegration.objects.get(user_id=user_id, service=service)
    except MCPIntegration.DoesNotExist:
        raise SystemExit(
            f"No {service!r} integration found for user_id={user_id}. "
            f"Has this user connected {service} via /settings/ yet?"
        )

    if not integration.access_token:
        raise SystemExit(f"Integration row exists but access_token is empty for user_id={user_id}.")

    return integration


def build_raw_message(to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def create_draft(access_token: str, raw_message: str) -> requests.Response:
    url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"message": {"raw": raw_message}}
    return requests.post(url, headers=headers, json=payload, timeout=20)


def main(user_id: int = 3):
    integration = get_valid_integration(user_id)

    raw_message = build_raw_message(
        to="ummna.star@gmail.com",
        subject="Test Draft from Django Shell",
        body="This is a test email draft generated via direct API execution.",
    )

    response = create_draft(integration.access_token, raw_message)
    try:
        body = response.json()
    except ValueError:
        return

    if response.status_code == 200:
        _ = body.get("id")
    elif response.status_code == 401:
        pass
    elif response.status_code == 403:
        pass
    else:
        pass


if __name__ == "__main__":
    main()

main(3)