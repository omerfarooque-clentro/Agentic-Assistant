import asyncio
from agent.integrations.access import (refresh_expired_google_token, validate_slack_integration)
from agent.models import MCPIntegration
from .grouping import build_user_tool_groups
from asgiref.sync import sync_to_async

async def get_user_tools(user):

    integrations = await sync_to_async(list)(MCPIntegration.objects.filter(user=user, enabled=True))
   
    if not integrations:
        return {}

    
    integrations = await asyncio.gather(
        *(refresh_expired_google_token(integration) for integration in integrations)
    )
    integrations = await asyncio.gather(
        *(validate_slack_integration(integration) for integration in integrations)
    )

    live_integrations = [integration for integration in integrations if integration is not None]
    
    if not live_integrations:
        return {}

    tool_groups = await build_user_tool_groups(live_integrations)

    return tool_groups