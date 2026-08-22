import os
from datetime import timedelta

import requests
from asgiref.sync import sync_to_async
from django.utils import timezone
 

GOOGLE_TOKEN_SERVICES = {"google", "gmail", "calendar", "docs", "sheets"}


@sync_to_async
def refresh_expired_google_token(integration):
    if (
        integration.service not in GOOGLE_TOKEN_SERVICES
        or not integration.refresh_token
        or not integration.expires_at
        or integration.expires_at > timezone.now() + timedelta(minutes=2)
    ):
        return integration

    print(f"i am refresh_expired_google_token and i am refreshing the token for service {integration.service} because it expired at {integration.expires_at}")

    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "refresh_token": integration.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    response.raise_for_status()
    token_data = response.json()
    integration.access_token = token_data["access_token"]
    integration.expires_at = timezone.now() + timedelta(
        seconds=token_data.get("expires_in", 3600)
    )
    integration.save(update_fields=["access_token", "expires_at", "updated_at"])
    return integration


def _is_slack_token_live(access_token):
    if not access_token:
        return False
    response = requests.post(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    return response.ok and response.json().get("ok", False)


@sync_to_async
def validate_slack_integration(integration):
    if integration.service != "slack":
        return integration

    try:
        is_live = _is_slack_token_live(integration.access_token)
    except requests.RequestException:
        # Do not disable an integration on transient network failures.
        return integration

    print(f"i am validate_slack_integration and the slack token is_live: {is_live}")

    if is_live:
        return integration

    integration.enabled = False
    integration.save(update_fields=["enabled", "updated_at"])
    return None
