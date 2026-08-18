import asyncio

from mcp_clients.common import get_tools as load_tools
from mcp_clients.common import google_workspace_config, print_tools


async def get_tools():
    """Return Gmail tools from the Google Workspace MCP server."""
    return await load_tools("gmail", google_workspace_config())


async def main() -> None:
    print_tools(await get_tools())


if __name__ == "__main__":
    asyncio.run(main())