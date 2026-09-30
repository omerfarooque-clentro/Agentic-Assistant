"""Connect-prompt handling for domains whose integration is not connected for the current user."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from agent.graph.state import AgentState

# Domains that require an explicit OAuth/API connection (sidebar Connect/Connected badges).
# "research" (web search + weather) is always available and never prompts to connect.
CONNECTABLE_DOMAINS = {"email", "calendar", "docs", "sheets", "slack"}

# domain -> human-readable service name shown to the user.
DOMAIN_SERVICE_LABEL = {
    "email": "Gmail",
    "calendar": "Google Calendar",
    "docs": "Google Docs",
    "sheets": "Google Sheets",
    "slack": "Slack",
}

# domain -> service key used by the existing /api/integrations/<service>/connect/ flow.
DOMAIN_SERVICE_KEY = {
    "email": "gmail",
    "calendar": "calendar",
    "docs": "docs",
    "sheets": "sheets",
    "slack": "slack",
}


def is_domain_disconnected(domain: str, available_domains: set[str] | None) -> bool:
    """Check whether a domain requires a connection the current user hasn't set up."""
    if domain not in CONNECTABLE_DOMAINS:
        return False
    return domain not in (available_domains or set())


def build_connect_prompt(domain: str) -> dict[str, Any]:
    """Build the structured connect-prompt signal shown instead of a data card."""
    label = DOMAIN_SERVICE_LABEL.get(domain, domain.title())
    service = DOMAIN_SERVICE_KEY.get(domain, domain)
    return {
        "kind": "connect_prompt",
        "domain": domain,
        "service": service,
        "label": label,
        "message": f"{label} isn't connected yet.",
    }


def connect_prompt_node(state: AgentState) -> dict[str, Any]:
    """Terminal node for a turn (or plan step) targeting a disconnected service.

    Never calls a tool or fabricates data. Emits one short sentence plus a
    structured connect_prompt signal the frontend renders as a dashed prompt.
    """
    domain = state.get("domain", "")
    prompt = build_connect_prompt(domain)
    ai_message = AIMessage(content=prompt["message"])
    updated_messages = state.get("messages", []) + [ai_message]

    output: dict[str, Any] = {
        "messages": updated_messages,
        "connect_prompts": [prompt],
    }

    plan = state.get("plan")
    if plan:
        updated_plan = [dict(s) for s in plan]
        for step in updated_plan:
            if step.get("status") == "in_progress":
                step["status"] = "completed"
                step["result_summary"] = prompt["message"]
        output["plan"] = updated_plan

    return output
