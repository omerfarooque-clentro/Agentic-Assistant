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


_DROPPED_SCHEMA_KEYS = {"examples", "patternProperties", "$schema", "title"}


def sanitize_tool_schema(schema: Any) -> dict[str, Any]:
    """Normalize a raw MCP JSON Schema into a shape OpenAI-style tool calling accepts.

    Inlines $defs/$ref, collapses oneOf/anyOf/allOf to their first variant, and
    strips keywords models like Groq's function calling don't reliably support.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    defs = schema.get("$defs") or schema.get("definitions") or {}

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and defs:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                target = defs.get(ref_name)
                if target is not None:
                    return resolve(dict(target))
            for key in ("oneOf", "anyOf", "allOf"):
                if key in node and node[key]:
                    return resolve(node[key][0])
            return {
                key: resolve(value)
                for key, value in node.items()
                if key not in _DROPPED_SCHEMA_KEYS and key != "$ref"
            }
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    resolved = resolve({k: v for k, v in schema.items() if k not in {"$defs", "definitions"}})
    resolved.setdefault("type", "object")
    resolved.setdefault("properties", {})
    return resolved


def sanitize_tool(tool: Any) -> Any:
    """Return a copy of a LangChain tool with a sanitized args_schema."""

    sanitized_schema = sanitize_tool_schema(getattr(tool, "args_schema", None))
    return tool.model_copy(update={"args_schema": sanitized_schema})


async def get_tools(name: str, config: Mapping[str, Any]) -> list[Any]:
    """Connect to one MCP server and return its sanitized LangChain tools."""
    tools = await create_client(name, config).get_tools()
    return [sanitize_tool(tool) for tool in tools]


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

    print(
        f"[MCP AUTH] service={getattr(integration, 'service', None)} "
        f"has_access_token={bool(access_token)} "
        f"url={os.getenv(env_var, default_url)}"
    )
    
    return {
        "url": os.getenv(env_var, default_url),
        "transport": "streamable_http",
        "headers": _headers("GOOGLE_ACCESS_TOKEN", "GOOGLE_OAUTH_ACCESS_TOKEN") if not access_token else {"Authorization": f"Bearer {access_token}"},
    }


# Each Google Workspace product ships its own dedicated MCP server; there is no shared endpoint.
def gmail_config(integration=None) -> dict[str, Any]:
    return _google_service_config(integration, "GMAIL_MCP_URL", "https://gmail.googleapis.com/mcp/v1")


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
    for tool in tools:
        print(tool.name)
        print(tool.description)