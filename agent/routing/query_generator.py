"""Contextual query generation using fast tiered model for reference resolution."""

from __future__ import annotations

import re
import time
from typing import TypedDict, Any
from langchain_core.prompts import ChatPromptTemplate

from agent.llm.prompts import QUERY_GENERATOR_PROMPT
from agent.llm.client import llm
from agent.metrics import CallMetrics, extract_call_metrics
from agent.routing.reference_detector import extract_message_text, has_conversational_reference


class ParsedPlanStep(TypedDict):
    domain: str
    query: str


class ParsedRoutingQuery(TypedDict):
    type: str
    query: str
    steps: list[ParsedPlanStep]
    metrics: CallMetrics | None


def heuristic_disambiguate_query(text: str, available_domains: set[str] | None = None) -> str:
    """Heuristically resolve queries like 'check latest message from arsalan' to Slack or Gmail."""
    clean = re.sub(r"^Date:[^,]+,\s*[^:]+:\s*", "", text or "", flags=re.IGNORECASE).strip()
    match = re.search(
        r"(?:check|find|get|show|read|see|fetch)\s+(?:the\s+)?(?:latest|recent|new|unread)?\s*(?:message|messages|msg|msgs|meesage|meesages)\s+(?:from|by)\s+([a-zA-Z0-9_\-\.]+)",
        clean,
        flags=re.IGNORECASE,
    )
    if match:
        person = match.group(1)
        if available_domains is None or "slack" in available_domains:
            return f"search slack for {person} latest message"
        elif "email" in available_domains:
            return f"search gmail for {person} latest message"
    return clean


def generate_routing_query(messages: Any, available_domains: set[str] | None = None) -> ParsedRoutingQuery:
    """Rewrite a contextual user message into a self-contained routing query using the fast 8B model."""
    print(f"Generating routing query for messages: {messages}")
    if isinstance(messages, (list, tuple)):
        message_list = list(messages)
    elif messages:
        message_list = [messages]
    else:
        message_list = []

    latest_message = message_list[-1] if message_list else None
    previous_messages = message_list[-3:-1] if len(message_list) > 1 else []

    if not latest_message:
        return {"type": "SINGLE", "query": "", "metrics": None}

    current_message_text = extract_message_text(latest_message)
    prev_text = (
        "\n".join(extract_message_text(m) for m in previous_messages)
        if previous_messages
        else "None"
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", QUERY_GENERATOR_PROMPT),
            (
                "human",
                "Previous context:\n{previous_messages}\n\nCurrent user message:\n{latest_message}",
            ),
        ]
    )

    formatted_prompt = prompt.format_prompt(
        previous_messages=prev_text,
        latest_message=current_message_text,
    ).to_messages()

    try:
        start_time = time.perf_counter()
        response = llm.invoke(formatted_prompt)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        raw_content = str(getattr(response, "content", "")).strip()

        # Check for PLAN:
        plan_match = re.search(r"PLAN:\s*(.*)", raw_content, re.IGNORECASE | re.DOTALL)
        if plan_match:
            plan_lines = plan_match.group(1).strip().splitlines()
            parsed_steps: list[ParsedPlanStep] = []
            for line in plan_lines:
                line_match = re.match(r"^\d+\.\s*\[(\w+)\]\s*(.*)", line.strip())
                if line_match:
                    domain = line_match.group(1).lower().strip()
                    step_query = line_match.group(2).strip()
                    if available_domains is None or domain in available_domains:
                        parsed_steps.append({"domain": domain, "query": step_query})
            if len(parsed_steps) >= 2:
                call_metrics = extract_call_metrics(
                    response=response,
                    step_name="Planner (Call #1)",
                    latency_ms=elapsed_ms,
                    model_name=getattr(llm, "model_name"),
                    prompt_text_or_messages=formatted_prompt,
                )
                return {
                    "type": "MULTI",
                    "query": parsed_steps[0]["query"],
                    "steps": parsed_steps,
                    "metrics": call_metrics,
                }

        # Clean any formatting prefixes like QUERY: <text>
        query_match = re.search(r"QUERY:\s*(.*)", raw_content, re.IGNORECASE | re.DOTALL)
        extracted_query = (query_match.group(1).strip() if query_match else raw_content).strip('"`\'')

        # Extract metrics via unified metrics module
        call_metrics = extract_call_metrics(
            response=response,
            step_name="Query Rewrite (Call #1)",
            latency_ms=elapsed_ms,
            model_name=getattr(llm, "model_name"),
            prompt_text_or_messages=formatted_prompt,
        )

        return {"type": "SINGLE", "query": extracted_query, "steps": [], "metrics": call_metrics}
    except Exception:
        fallback_query = heuristic_disambiguate_query(current_message_text, available_domains)
        print(f"Fallback routing query: {fallback_query}")
        return {"type": "SINGLE", "query": fallback_query, "steps": [], "metrics": None}