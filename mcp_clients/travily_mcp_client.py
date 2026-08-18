import asyncio

from mcp_clients.common import get_tools as load_tools
from mcp_clients.common import print_tools, tavily_config


async def get_tools():
    """Return web-search tools from the Tavily MCP server."""
    return await load_tools("web", tavily_config())


async def main() -> None:
    print_tools(await get_tools())


if __name__ == "__main__":
    asyncio.run(main())