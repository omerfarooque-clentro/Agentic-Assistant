import asyncio

from mcp_clients.common import get_tools as load_tools
from mcp_clients.common import docs_config, print_tools


async def get_tools():
    """Return Google Docs tools from the dedicated Docs MCP server."""
    return await load_tools("docs", docs_config())


async def main() -> None:
    print_tools(await get_tools())


if __name__ == "__main__":
    asyncio.run(main())