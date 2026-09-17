"""Group MCP tools by domain for a set of user integrations."""

import asyncio
import logging

from mcp_clients.common import get_mcp_config, get_tools

from .domain_registry import resolve_tool_domain

logger = logging.getLogger(__name__)


async def _fetch_tools_for_integration(integration):
    """Fetch tools from one MCP integration, returning the error on failure."""
    try:
        config = get_mcp_config(integration)
        return await get_tools(integration.service, config)
    except Exception as error:
        logger.warning("Failed to fetch tools for service %s: %s", integration.service, error)
        return error


async def _fetch_tools_for_integrations_concurrently(integrations):
    """Fetch tools from all integrations in parallel."""
    tasks = [_fetch_tools_for_integration(item) for item in integrations]
    return await asyncio.gather(*tasks)


def _bucket_tools_by_domain(integrations, results):
    """Assign each tool to its domain bucket, deduplicating by name."""
    seen_tool_names = set()
    groups = {}

    for integration, tools in zip(integrations, results):
        if isinstance(tools, Exception):
            continue

        for tool in tools:
            if tool.name in seen_tool_names:
                continue
            seen_tool_names.add(tool.name)

            domain = resolve_tool_domain(integration.service, tool.name)
            groups.setdefault(domain, []).append(tool)

    return groups


async def build_user_tool_groups(integrations):
    """Build domain-grouped tool dictionary for a list of live integrations."""
    results = await _fetch_tools_for_integrations_concurrently(integrations)
    return _bucket_tools_by_domain(integrations, results)
