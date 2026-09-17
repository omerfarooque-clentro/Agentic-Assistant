"""Graph execution nodes for intent routing, domain agent execution, and thread naming."""

from __future__ import annotations

import logging

import time
from typing import Any

from agent.llm import llm
from agent.llm.messages import messages_for_llm
from agent.graph.approval import APPROVAL_TOOL_NAMES
from agent.graph.state import AgentState
from agent.metrics import extract_call_metrics, DEFAULT_MODEL
from agent.routing.intent_router import route_intent

logger = logging.getLogger(__name__)


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
    if "plan" in result and result["plan"]:
        has_active = any(
            isinstance(s, dict) and s.get("status") in ("pending", "in_progress")
            for s in result["plan"]
        )
        if has_active:
            output["plan"] = result["plan"]
            output["current_step_index"] = state.get("current_step_index", 0)
        else:
            output["plan"] = []
            output["current_step_index"] = 0
    else:
        output["plan"] = []
        output["current_step_index"] = 0
    return output


def advance_plan_node(state: AgentState) -> dict[str, Any]:
    """Advance the current plan step to completed and prepare the next pending step."""
    plan = [dict(s) for s in state.get("plan", [])]
    logger.debug("plan: %s", plan)
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

    raw_metrics = state.get("call_metrics") or []
    valid_calls = [m for m in raw_metrics if isinstance(m, dict) and not m.get("__reset__")]
    call_count = len(valid_calls) + 1

    call_metric = extract_call_metrics(
        response=response,
        step_name=f"{domain.capitalize()} Agent (Call #{call_count})",
        latency_ms=elapsed_ms,
        model_name=getattr(llm_with_tools, "model_name", DEFAULT_MODEL),
        prompt_text_or_messages=messages,
    )

    updated_messages = state.get("messages", []) + [response]
    output: dict[str, Any] = {
        "messages": updated_messages,
        "call_metrics": [call_metric],
    }

    # If the agent finished its turn without requesting tool execution,
    # and no further pending steps remain in the plan, mark the in-progress step completed.
    plan = state.get("plan")
    if plan:
        has_tool_calls = bool(getattr(response, "tool_calls", None))
        has_pending = any(isinstance(s, dict) and s.get("status") == "pending" for s in plan)
        if not has_tool_calls and not has_pending:
            updated_plan = [dict(s) for s in plan]
            for s in updated_plan:
                if s.get("status") == "in_progress":
                    s["status"] = "completed"
                    content = getattr(response, "content", "")
                    if isinstance(content, str):
                        s["result_summary"] = content.strip()[:300]
            output["plan"] = updated_plan

    return output


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