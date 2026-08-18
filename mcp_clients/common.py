import os
from collections.abc import Mapping
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient


def _headers(*token_names: str) -> dict[str, str]:
    for token_name in token_names:
        token = os.getenv(token_name)
        if token:
            return {"Authorization": f"Bearer {token}"}
    return {}


def create_client(name: str, config: Mapping[str, Any]) -> MultiServerMCPClient:
    """Create a client without opening a network connection."""
    return MultiServerMCPClient({name: dict(config)})


async def get_tools(name: str, config: Mapping[str, Any]) -> list[Any]:
    """Connect to one MCP server and return its LangChain tools."""
    return await create_client(name, config).get_tools()

def get_mcp_config(integration):
    if integration.service == "google":
        return google_workspace_config(integration)

    if integration.service == "tavily":
        return tavily_config(integration)

    if integration.service == "slack":
        return slack_config(integration)

    raise ValueError(
        f"Unsupported MCP service: {integration.service}"
    )

def google_workspace_config(integration) -> dict[str, Any]:
    return {
        "url": os.getenv(
            "GOOGLE_WORKSPACE_MCP_URL",
            "https://workspace.google.com/mcp",
        ),
        "transport": "streamable_http",
        "headers": {
            "Authorization": f"Bearer {integration.access_token}",
        }
    }


def slack_config(integration) -> dict[str, Any]:
    return {
        "url": os.getenv("SLACK_MCP_URL", "https://mcp.slack.com/mcp"),
        "transport": "streamable_http",
        "headers": {
            "Authorization": f"Bearer {integration.access_token}",
        }
    }


def tavily_config(integration) -> dict[str, Any]:
    config = {
        "url": os.getenv("TAVILY_MCP_URL", "https://mcp.tavily.com/mcp/"),
        "transport": "streamable_http",
        "headers": {},
    }
    api_key = os.getenv("TAVILY_API_KEY")
    if api_key:
        config["url"] = f"{config['url']}?tavilyApiKey={api_key}"
    return config


def print_tools(tools: list[Any]) -> None:
    for tool in tools:
        print(tool.name)
        print(tool.description)