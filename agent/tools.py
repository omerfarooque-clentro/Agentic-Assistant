import asyncio
from .integration_access import get_enabled_integrations, refresh_expired_google_token
from .tool_grouping import build_user_tool_groups

async def get_user_tools(user):
    integrations = await get_enabled_integrations(user)

    if not integrations:
        return {}

    integrations = await asyncio.gather(
        *(refresh_expired_google_token(integration) for integration in integrations)
    )
    return await build_user_tool_groups(integrations)