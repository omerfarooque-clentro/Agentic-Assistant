"""Contextual query generation using fast tiered model for reference resolution."""

from __future__ import annotations

import re
import time
from typing import TypedDict, Any
from langchain_core.prompts import ChatPromptTemplate

from agent.llm.prompts import QUERY_GENERATOR_PROMPT
from agent.llm.client import llm_fast
from agent.metrics import CallMetrics, extract_call_metrics, DEFAULT_FAST_MODEL
from agent.routing.reference_detector import extract_message_text, has_conversational_reference


class ParsedRoutingQuery(TypedDict):
    type: str
    query: str
    metrics: CallMetrics | None


def generate_routing_query(messages: Any) -> ParsedRoutingQuery:
    """Rewrite a contextual user message into a self-contained routing query using the fast 8B model."""
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

    start_time = time.perf_counter()
    response = llm_fast.invoke(formatted_prompt)
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    raw_content = str(getattr(response, "content", "")).strip()

    # Clean any formatting prefixes like QUERY: <text>
    query_match = re.search(r"QUERY:\s*(.*)", raw_content, re.IGNORECASE | re.DOTALL)
    extracted_query = (query_match.group(1).strip() if query_match else raw_content).strip('"`\'')

    # Extract metrics via unified metrics module
    call_metrics = extract_call_metrics(
        response=response,
        step_name="Query Rewrite (Call #1)",
        latency_ms=elapsed_ms,
        model_name=getattr(llm_fast, "model_name", DEFAULT_FAST_MODEL),
        prompt_text_or_messages=formatted_prompt,
    )

    return {"type": "SINGLE", "query": extracted_query, "metrics": call_metrics}