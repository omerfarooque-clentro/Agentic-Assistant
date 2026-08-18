from .models import MCPIntegration
from mcp_clients.common import get_tools, get_mcp_config
from asgiref.sync import sync_to_async
import asyncio


@sync_to_async
def get_enabled_integrations(user):
    return list(MCPIntegration.objects.filter(
        user=user,
        enabled=True,
    )
    )


async def get_user_tools(user):

    integrations = await get_enabled_integrations(user)

    if not integrations:
        return []

    tasks = [
        get_tools(
            integration.service,
            get_mcp_config(integration),
        )
        for integration in integrations
    ]

    results = await asyncio.gather(*tasks)

    return [
        tool
        for sublist in results
        for tool in sublist
    ]