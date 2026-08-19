import asyncio

from mcp_clients.common import get_tools as load_tools
from mcp_clients.common import calendar_config, print_tools


async def load_tools():
	"""Return Google Calendar tools from the dedicated Calendar MCP server."""
	return await load_tools("calendar", calendar_config())


async def main() -> None:
	print_tools(await load_tools())


if __name__ == "__main__":
	asyncio.run(main())
