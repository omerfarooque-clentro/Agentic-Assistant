import os
from datetime import timedelta
from urllib.parse import urlencode
import requests
from django.core import signing
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from agent.models import MCPIntegration


STATE_SALT = "mcp-integration-oauth"
STATE_MAX_AGE = 600

GOOGLE_OPENID_SCOPES = "openid email profile"

# Each Google product requests only its own scopes, not the full Workspace set.
GOOGLE_SERVICE_SCOPES = {
    "gmail": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.labels",
    "calendar": "https://www.googleapis.com/auth/calendar",
    "docs": "https://www.googleapis.com/auth/documents",
    "sheets": "https://www.googleapis.com/auth/spreadsheets",
}
GOOGLE_SERVICES = {"gmail", "calendar", "docs", "sheets"}

SLACK_SCOPES = ["channels:read", "im:read", "mpim:read", "users:read", "chat:write", "reactions:read", "reactions:write"]

def _google_scopes(service):
    service_scope = GOOGLE_SERVICE_SCOPES.get(service, "")
    return f"{GOOGLE_OPENID_SCOPES} {service_scope}".strip()


def _redirect_uri(request, service):
    provider = "google" if service in GOOGLE_SERVICES else service
    configured = os.getenv(f"{provider.upper()}_OAUTH_REDIRECT_URI")
    return configured or request.build_absolute_uri(f"/api/integrations/{provider}/callback/")


def _signed_state(user_id, service):
    provider = "google" if service in GOOGLE_SERVICES else service
    return signing.dumps(
        {"user_id": user_id, "service": service, "provider": provider},
        salt=STATE_SALT,
    )


def _read_state(value):
    return signing.loads(value, salt=STATE_SALT, max_age=STATE_MAX_AGE)


def _provider_config(request, service):
    state = _signed_state(request.user.id, service)
    redirect_uri = _redirect_uri(request, service)

    if service == "google" or service in GOOGLE_SERVICES:
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        if not client_id:
            raise RuntimeError("GOOGLE_CLIENT_ID is not configured")
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _google_scopes(service),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        })

    client_id = os.getenv("SLACK_CLIENT_ID")
    if not client_id:
        raise RuntimeError("SLACK_CLIENT_ID is not configured")
    return "https://slack.com/oauth/v2_user/authorize?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": ",".join(SLACK_SCOPES),
        "state": state,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def integration_connect_view(request, service):
    if service not in GOOGLE_SERVICES | {"google", "slack"}:
        return JsonResponse({"detail": "Unsupported integration."}, status=400)
    try:
        return JsonResponse({"authorization_url": _provider_config(request, service)})
    except RuntimeError as error:
        return JsonResponse({"detail": str(error)}, status=503)



def _refresh_google_access_token(integration):
    if not integration.refresh_token:
        return False
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "refresh_token": integration.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    if not response.ok:
        return False
    token_data = response.json()
    integration.access_token = token_data["access_token"]
    integration.expires_at = timezone.now() + timedelta(seconds=token_data.get("expires_in", 3600))
    integration.save(update_fields=["access_token", "expires_at", "updated_at"])
    return True


def _is_google_token_live(integration):
    response = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"access_token": integration.access_token},
        timeout=10,
    )
    if response.ok:
        return True
    # Access token expired or revoked — try to mint a fresh one before giving up.
    return _refresh_google_access_token(integration)


def _is_slack_token_live(integration):
    response = requests.post(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {integration.access_token}"},
        timeout=10,
    )
    return response.ok and response.json().get("ok", False)


def _integration_is_live(integration):
    try:
        if integration.service in GOOGLE_SERVICES:
            return _is_google_token_live(integration)
        if integration.service == "slack":
            return _is_slack_token_live(integration)
        return integration.enabled
    except requests.RequestException:
        # Provider unreachable — fall back to the last known state rather than
        # flipping a healthy integration to "disconnected".
        return integration.enabled


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def integration_status_view(request):
    integrations = MCPIntegration.objects.filter(user=request.user)
    payload = []
    for item in integrations:
        live = _integration_is_live(item) if item.enabled else False
        if item.enabled and not live:
            # MCP/provider access was revoked outside of our disconnect flow.
            item.enabled = False
            item.save(update_fields=["enabled", "updated_at"])
        payload.append({"service": item.service, "enabled": live})
    return JsonResponse({"integrations": payload})


def _exchange_code(service, code, redirect_uri):
    if service == "google" or service in GOOGLE_SERVICES:
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
    else:
        response = requests.post(
            "https://slack.com/api/oauth.v2.user.access",
            data={
                "code": code,
                "client_id": os.getenv("SLACK_CLIENT_ID"),
                "client_secret": os.getenv("SLACK_CLIENT_SECRET"),
                "redirect_uri": redirect_uri,
            },
            timeout=20,
        )
    response.raise_for_status()
    token_data = response.json()

    if service == "slack" and not token_data.get("ok"):
        raise RuntimeError(token_data.get("error", "Slack OAuth failed"))
    return token_data


def integration_callback_view(request, service):
    if request.GET.get("error"):
        return JsonResponse({"detail": request.GET["error"]}, status=400)
    try:
        state = _read_state(request.GET["state"])
        if state["provider"] != service:
            raise ValueError("OAuth service mismatch")
        requested_service = state["service"]
        provider = service
        token_data = _exchange_code(
            provider,
            request.GET["code"],
            _redirect_uri(request, provider),
        )
        authed_user = token_data.get("authed_user", {})
        access_token = token_data.get("access_token") or authed_user.get("access_token")
        if not access_token:
            raise ValueError("OAuth provider returned no access token")

        scopes = token_data.get("scope") or authed_user.get("scope", "")
        existing = MCPIntegration.objects.filter(
            user_id=state["user_id"],
            service=requested_service,
        ).first()
        refresh_token = token_data.get("refresh_token") or (
            existing.refresh_token if existing else None
        )
        user_integration, _ = MCPIntegration.objects.update_or_create(
            user_id=state["user_id"],
            service=requested_service,
            defaults={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "scopes": " ".join(scopes.split()) if scopes else "",
                "expires_at": timezone.now() + timedelta(seconds=token_data.get("expires_in", 3600)),
                "enabled": True,
            },
        )
    except (KeyError, signing.BadSignature, requests.RequestException, ValueError, RuntimeError) as error:
        return JsonResponse({"detail": str(error)}, status=400)
    return redirect(f"/?connected={user_integration.service}")


@csrf_exempt
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def integration_disconnect_view(request, service):
    """
    Disconnects the integration by revoking the OAuth token with Google/Slack
    and removing the database record.
    """
    try:
        integration = MCPIntegration.objects.get(user=request.user, service=service)

        # 1. Revoke token with Google if service is a Google service
        if service == "google" or service in GOOGLE_SERVICES:
            token_to_revoke = integration.refresh_token or integration.access_token
            if token_to_revoke:
                try:
                    requests.post(
                        "https://oauth2.googleapis.com/revoke",
                        params={"token": token_to_revoke},
                        headers={"content-type": "application/x-www-form-urlencoded"},
                        timeout=10,
                    )
                except requests.RequestException:
                    pass  # Continue to delete DB row even if Google revoke call fails

        # 2. Revoke the Slack user token.
        elif service == "slack" and integration.access_token:
            try:
                requests.post(
                    "https://slack.com/api/auth.revoke",
                    headers={"Authorization": f"Bearer {integration.access_token}"},
                    timeout=10,
                )
            except requests.RequestException:
                pass

        # 3. Delete database record
        integration.delete()
        return JsonResponse({"service": service, "disconnected": True})

    except MCPIntegration.DoesNotExist:
        return JsonResponse({"service": service, "disconnected": False}, status=404)