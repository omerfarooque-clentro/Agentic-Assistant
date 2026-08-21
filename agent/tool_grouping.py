import asyncio

from mcp_clients.common import get_mcp_config, get_tools

from .tool_domain_registry import resolve_tool_domain


DEFAULT_GROUPS = {
    "email": [],
    "calendar": [],
    "docs": [],
    "sheets": [],
    "slack": [],
    "research": [],
}


def _unique_integrations_by_service(integrations):
    unique_integrations = []
    seen_services = set()

    for integration in integrations:
        if integration.service in seen_services:
            continue
        seen_services.add(integration.service)
        unique_integrations.append(integration)

    return unique_integrations


async def _fetch_tools_for_integrations(integrations):
    tasks = [get_tools(item.service, get_mcp_config(item)) for item in integrations]
    return await asyncio.gather(*tasks, return_exceptions=True)


def _bucket_tools_by_domain(integrations, results):
    groups = {domain: list(tools) for domain, tools in DEFAULT_GROUPS.items()}
    seen_tool_names = set()

    for integration, tools in zip(integrations, results):
        if isinstance(tools, Exception):
            continue

        for tool in tools:
            if tool.name in seen_tool_names:
                continue
            seen_tool_names.add(tool.name)

            domain = resolve_tool_domain(integration.service, tool.name)
            groups.setdefault(domain, []).append(tool)

    return {
        domain: tools
        for domain, tools in groups.items()
        if tools
    }


async def build_user_tool_groups(integrations):
    unique_integrations = _unique_integrations_by_service(integrations)
    results = await _fetch_tools_for_integrations(unique_integrations)
    return _bucket_tools_by_domain(unique_integrations, results)
