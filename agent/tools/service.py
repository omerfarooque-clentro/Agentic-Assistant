import asyncio
from agent.integrations.access import (refresh_expired_google_token, validate_slack_integration)
from agent.models import MCPIntegration
from .grouping import build_user_tool_groups
from asgiref.sync import sync_to_async

async def get_user_tools(user):

    integrations = await sync_to_async(list)(MCPIntegration.objects.filter(user=user, enabled=True))
   # print(f"i am get_user_tools and i am fetching tools for user {user.id} with integrations: {[i.service for i in integrations]}")
    if not integrations:
        return {}

    
    integrations = await asyncio.gather(
        *(refresh_expired_google_token(integration) for integration in integrations)
    )
    integrations = await asyncio.gather(
        *(validate_slack_integration(integration) for integration in integrations)
    )

    live_integrations = [integration for integration in integrations if integration is not None]
  #  print(f"i am get_user_tools and i am fetching tools for user {user.id} with live integrations: {[i.service for i in live_integrations]}")
    if not live_integrations:
        return {}

    tool_groups = await build_user_tool_groups(live_integrations)
   # print(f"i am get_user_tools and i am fetching tools for user {user.id} with tool groups: {list(tool_groups.values())}")
    return tool_groups