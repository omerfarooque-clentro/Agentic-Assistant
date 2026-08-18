import asyncio

from mcp_clients.common import get_tools as load_tools
from mcp_clients.common import print_tools, slack_config


async def get_tools():
	"""Return Slack tools from the Slack MCP server."""
	return await load_tools("slack", slack_config())


async def main() -> None:
	print_tools(await get_tools())


if __name__ == "__main__":
	asyncio.run(main())
