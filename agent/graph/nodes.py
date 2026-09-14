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
    plan = state.get("plan", [])
    result = route_intent(messages, available_domains=available_domains, plan=plan)

    call_metrics = [result["call_metrics"]] if result.get("call_metrics") else []

    output = {
        "intent": result["intent"],
        "domain": result["domain"],
        "confidence": result["confidence"],
        "margin": result["margin"],
        "routing_status": result["status"],
        "call_metrics": call_metrics,
    }
    if "plan" in result:
        output["plan"] = result["plan"]
        output["current_step_index"] = state.get("current_step_index", 0)
    return output


def advance_plan_node(state: AgentState) -> dict[str, Any]:
    """Advance the current plan step to completed and prepare the next pending step."""
    plan = [dict(s) for s in state.get("plan", [])]
    print(f"plan: {plan}")
    current_idx = state.get("current_step_index", 0)

    # Extract summary/result from last agent message or tool message
    messages = state.get("messages", [])
    last_content = ""
    for msg in reversed(messages):
        if getattr(msg, "content", None):
            if isinstance(msg.content, str) and msg.content.strip():
                last_content = msg.content.strip()
                break

    # Mark current in_progress step as completed
    for step in plan:
        if step.get("status") == "in_progress":
            step["status"] = "completed"
            step["result_summary"] = last_content[:300]
            break

    # Find next pending step
    next_idx = current_idx + 1
    next_step = next((s for s in plan if s.get("status") == "pending"), None)
    if next_step:
        next_step["status"] = "in_progress"

    return {
        "plan": plan,
        "current_step_index": next_idx,
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
    """Determine if execution should end, proceed to tools, advance plan, or require approval."""
    messages = state.get("messages", [])
    if not messages:
        return "end"

    last_message = messages[-1]
    tool_calls = getattr(last_message, "tool_calls", None)
    if tool_calls:
        if domain in APPROVAL_DOMAINS:
            for tool_call in tool_calls:
                if tool_call.get("name") in APPROVAL_TOOL_NAMES:
                    return "approval"
        return "tools"

    # No pending tool calls. Check if there are remaining pending steps in the plan.
    plan = state.get("plan", [])
    has_more_steps = any(isinstance(s, dict) and s.get("status") == "pending" for s in plan)
    if has_more_steps:
        return "advance_plan"

    return "end"