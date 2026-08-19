from .models import MCPIntegration
from mcp_clients.common import get_tools, get_mcp_config
from asgiref.sync import sync_to_async
import asyncio
import os
import requests
from datetime import timedelta
from django.utils import timezone


@sync_to_async
def get_enabled_integrations(user):
    return list(MCPIntegration.objects.filter(
        user=user,
        enabled=True,
    ))


@sync_to_async
def refresh_expired_google_token(integration):
    if (
        integration.service not in {"google", "gmail", "calendar", "docs", "sheets"}
        or not integration.refresh_token
        or not integration.expires_at
        or integration.expires_at > timezone.now()
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

SERVICE_TO_DOMAIN = {
    "gmail": "email",
    "calendar": "calendar",
    "docs": "docs",
    "sheets": "sheets",
    "slack": "slack",
    "tavily": "research",
    "brave": "research",
}


def get_domain_for_service(service):
    return SERVICE_TO_DOMAIN.get(service, service)

async def get_user_tools(user):

    print(f"[TOOLS] Loading enabled integrations for user={getattr(user, 'id', user)}")
    integrations = await get_enabled_integrations(user)
    print(f"[TOOLS] Enabled integrations: {[i.service for i in integrations]}")

    if not integrations:
        print("[TOOLS] No integrations enabled -> empty tool groups")
        return {}

    integrations = await asyncio.gather(
        *(refresh_expired_google_token(integration) for integration in integrations)
    )
    tasks = []
    task_integrations = []
    seen_services = set()
    for integration in integrations:
        print(
            "[GOOGLE] service=", integration.service,
            "has_access_token=", bool(integration.access_token),
            "has_refresh_token=", bool(integration.refresh_token),
            "expires_at=", integration.expires_at,
        )
        # Each Google product (gmail/calendar/docs/sheets) is its own dedicated
        # MCP server, so only dedupe exact duplicate service rows, not the
        # whole "google" provider.
        if integration.service in seen_services:
            continue
        seen_services.add(integration.service)
        task_integrations.append(integration)
        tasks.append(get_tools(integration.service, get_mcp_config(integration)))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    groups= {
        "email" : [],
        "calendar" : [],
        "docs" : [],
        "sheets" : [],
        "slack" : [],
        "research" : [],
    }

    for integration, tools in zip(task_integrations, results):
        print(f"{tools=} , {integration=}")
        if isinstance(tools, Exception):
            print(
            f"[TOOLS] FAILED: service={integration.service}"
            )
            print(
                f"[TOOLS] ERROR: {type(tools).__name__}: {tools}"
            )
            continue


        print(
            f"[TOOLS] service={integration.service} "
            f"returned {len(tools)} tools"
        )
        for tool in tools:
            print(f"[TOOLS]   - {tool.name} (domain={get_domain_for_service(integration.service)})")

        domain = get_domain_for_service(integration.service)
        groups[domain].extend(tools)

    final_groups = {
        domain: tools
        for domain, tools in groups.items()
        if tools
    }
    print(f"[TOOLS] Domain tool groups: { {k: [t.name for t in v] for k, v in final_groups.items()} }")
    return final_groups