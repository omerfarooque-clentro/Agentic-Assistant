"""Service layer for fetching and assembling per-user MCP tool groups."""

import asyncio
import logging

from asgiref.sync import sync_to_async

from agent.cards.weather_tool import get_weather
from agent.integrations.access import refresh_expired_google_token, validate_slack_integration
from agent.models import MCPIntegration
from .grouping import build_user_tool_groups
from .slack_resolver import create_slack_resolver_tool

logger = logging.getLogger(__name__)


async def get_user_tools(user):
    """Fetch, refresh, and group MCP tools for the given user."""
    integrations = await sync_to_async(list)(
        MCPIntegration.objects.filter(user=user, enabled=True)
    )
    tool_groups = {}
    if integrations:
        integrations = await asyncio.gather(
            *(refresh_expired_google_token(integration) for integration in integrations)
        )
        integrations = await asyncio.gather(
            *(validate_slack_integration(integration) for integration in integrations)
        )

        live_integrations = [integration for integration in integrations if integration is not None]
        if live_integrations:
            tool_groups = await build_user_tool_groups(live_integrations)
            if "slack" in tool_groups:
                tool_groups["slack"].append(create_slack_resolver_tool(user))

    tool_groups.setdefault("research", []).append(get_weather)
    return tool_groups