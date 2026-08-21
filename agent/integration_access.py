import os
from datetime import timedelta

import requests
from asgiref.sync import sync_to_async
from django.utils import timezone

from .models import MCPIntegration


GOOGLE_TOKEN_SERVICES = {"google", "gmail", "calendar", "docs", "sheets"}


@sync_to_async
def get_enabled_integrations(user):
    return list(
        MCPIntegration.objects.filter(
            user=user,
            enabled=True,
        )
    )


@sync_to_async
def refresh_expired_google_token(integration):
    if (
        integration.service not in GOOGLE_TOKEN_SERVICES
        or not integration.refresh_token
        or not integration.expires_at
        or integration.expires_at > timezone.now() + timedelta(minutes=2)
    ):
        return integration

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
