import asyncio

from mcp_clients.common import get_mcp_config, get_tools

from .domain_registry import resolve_tool_domain



async def _fetch_tools_for_integration(integration):
    try:
        config = get_mcp_config(integration)
        return await get_tools(integration.service, config)
    except Exception as error:
        return error


async def _fetch_tools_for_integrations_concurrently(integrations):
    tasks = [_fetch_tools_for_integration(item) for item in integrations]
    #print(f"i am _fetch_tools_for_integrations and i am fetching tools for integrations: {[i.service for i in integrations]}")
    return await asyncio.gather(*tasks)


def _bucket_tools_by_domain(integrations, results):
    seen_tool_names = set()
    groups = {}

    for integration, tools in zip(integrations, results):
        if isinstance(tools, Exception):
            #print(f"i am _bucket_tools_by_domain and fetching tools for service {integration.service} failed: {tools}")
            continue

        for tool in tools:
            if tool.name in seen_tool_names:
                continue
            seen_tool_names.add(tool.name)

            domain = resolve_tool_domain(integration.service, tool.name)
            groups.setdefault(domain, []).append(tool)

    return groups


async def build_user_tool_groups(integrations):
    results = await _fetch_tools_for_integrations_concurrently(integrations)#MCP tools for each integration
    return _bucket_tools_by_domain(integrations, results)
