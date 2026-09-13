"""Graph execution nodes for intent routing, domain agent execution, and thread naming."""

from __future__ import annotations

import time
from typing import Any

from agent.llm import llm
from agent.llm.messages import messages_for_llm
from agent.graph.approval import APPROVAL_TOOL_NAMES
from agent.graph.state import AgentState
from agent.metrics import extract_call_metrics, DEFAULT_MODEL
from agent.routing.intent_router import route_intent


# Domains whose write actions are gated behind human-in-the-loop approval.
APPROVAL_DOMAINS = {"email", "calendar", "docs", "sheets", "slack"}


def nlp_node(state: AgentState, available_domains=()) -> dict[str, Any]:
    """Execute intent classification and record any routing call metrics."""
    messages = state.get("messages", [])
    result = route_intent(messages, available_domains=available_domains)

    call_metrics = [result["call_metrics"]] if result.get("call_metrics") else []

    return {
        "intent": result["intent"],
        "domain": result["domain"],
        "confidence": result["confidence"],
        "margin": result["margin"],
        "routing_status": result["status"],
        "call_metrics": call_metrics,
    }


def agent_node(state: AgentState, llm_with_tools: Any, domain: str = "general") -> dict[str, Any]:
    """Execute domain-scoped LLM reasoning with tools and record Call #2 metrics."""
    messages = messages_for_llm(state, domain=domain)
    start_time = time.perf_counter()
    response = llm_with_tools.invoke(messages)
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    call_metric = extract_call_metrics(
        response=response,
        step_name=f"{domain.capitalize()} Agent (Call #2)",
        latency_ms=elapsed_ms,
        model_name=getattr(llm_with_tools, "model_name", DEFAULT_MODEL),
        prompt_text_or_messages=messages,
    )

    updated_messages = state.get("messages", []) + [response]
    return {
        "messages": updated_messages,
        "call_metrics": [call_metric],
    }


def supervisor_router(state: AgentState) -> str:
    """Route to appropriate domain agent or fallback to general."""
    if state.get("routing_status") == "unavailable":
        return "general"
    return state.get("domain", "general")


def scoped_should_continue(state: AgentState, domain: str) -> str:
    """Determine if execution should end, proceed to tools, or require approval."""
    messages = state.get("messages", [])
    if not messages:
        return "end"

    last_message = messages[-1]
    tool_calls = getattr(last_message, "tool_calls", None)
    if not tool_calls:
        return "end"

    if domain in APPROVAL_DOMAINS:
        for tool_call in tool_calls:
            if tool_call.get("name") in APPROVAL_TOOL_NAMES:
                return "approval"

    return "tools"