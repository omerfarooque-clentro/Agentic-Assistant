import os
from collections.abc import Mapping
from typing import Any

import httpx
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
    """Connect to one MCP server and return its sanitized LangChain tools."""
 
    try:
        tools = await create_client(name, config).get_tools()
    except httpx.HTTPStatusError as error:
        raise
   # print("MCP tools loaded:", {"service": name, "tools:": [tool.name for tool in tools]})
    return tools


def get_mcp_config(integration):
    if integration.service in {"gmail", "google"}:
        return gmail_config(integration)

    if integration.service == "calendar":
        return calendar_config(integration)

    if integration.service == "docs":
        return docs_config(integration)

    if integration.service == "sheets":
        return sheets_config(integration)

    if integration.service == "tavily":
        return tavily_config(integration)

    if integration.service == "slack":
        return slack_config(integration)

    raise ValueError(
        f"Unsupported MCP service: {integration.service}"
    )


def _google_service_config(integration, env_var, default_url):
    access_token = getattr(integration, "access_token", None)

    return {
        "url": os.getenv(env_var, default_url),
        "transport": "streamable_http",
        "headers": _headers("GOOGLE_ACCESS_TOKEN", "GOOGLE_OAUTH_ACCESS_TOKEN") if not access_token else {"Authorization": f"Bearer {access_token}"},
    }


# Each Google Workspace product ships its own dedicated MCP server; there is no shared endpoint.
def gmail_config(integration=None) -> dict[str, Any]:
    return _google_service_config(integration, "GMAIL_MCP_URL", "http://127.0.0.1:8001/mcp")


def calendar_config(integration=None) -> dict[str, Any]:
    return _google_service_config(integration, "CALENDAR_MCP_URL", "https://calendar.googleapis.com/mcp")


def docs_config(integration=None) -> dict[str, Any]:
    return _google_service_config(integration, "DOCS_MCP_URL", "https://docs.googleapis.com/mcp")


def sheets_config(integration=None) -> dict[str, Any]:
    return _google_service_config(integration, "SHEETS_MCP_URL", "https://sheets.googleapis.com/mcp")


def slack_config(integration=None) -> dict[str, Any]:
    access_token = getattr(integration, "access_token", None)
    
    return {
        "url": os.getenv("SLACK_MCP_URL", "https://mcp.slack.com/mcp"),
        "transport": "streamable_http",
        "headers": _headers("SLACK_BOT_TOKEN", "SLACK_ACCESS_TOKEN") if not access_token else {"Authorization": f"Bearer {access_token}"},
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
    _ = tools