"""Shared constants for the agent package."""

# Node names whose streamed tokens are forwarded to the user.
AGENT_NODES = frozenset({
    "general_agent",
    "email_agent",
    "calendar_agent",
    "docs_agent",
    "sheets_agent",
    "slack_agent",
    "research_agent",
})
