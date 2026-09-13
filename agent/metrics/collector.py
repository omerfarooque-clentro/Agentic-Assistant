"""Metrics collection, token extraction, and turn-level performance aggregation."""

from __future__ import annotations

import time
from typing import Any
from agent.metrics.types import (
    CallMetrics,
    TurnMetrics,
    CONTEXT_WINDOW_LIMIT,
    DEFAULT_MODEL,
    DEFAULT_FAST_MODEL,
)


def estimate_tokens(text: Any) -> int:
    """Fast character-based heuristic token estimator (~4 characters per token)."""
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    return max(1, len(text) // 4)


def extract_call_metrics(
    response: Any,
    step_name: str,
    latency_ms: float,
    model_name: str | None = None,
    prompt_text_or_messages: Any = None,
) -> CallMetrics:
    """Extract standard token counts and latency from an LLM response with resilient heuristics."""
    usage = (
        getattr(response, "usage_metadata", None)
        or getattr(response, "response_metadata", {}).get("token_usage", {})
        or {}
    )

    # Fallback estimations if provider metadata is absent
    raw_content = getattr(response, "content", "") or str(response or "")
    fallback_output = estimate_tokens(raw_content)

    if prompt_text_or_messages is not None:
        if isinstance(prompt_text_or_messages, (list, tuple)):
            prompt_str = " ".join(
                getattr(m, "content", "") if hasattr(m, "content") else str(m)
                for m in prompt_text_or_messages
            )
        else:
            prompt_str = str(prompt_text_or_messages)
        fallback_input = estimate_tokens(prompt_str)
    else:
        fallback_input = 0

    input_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or fallback_input
        or 1
    )
    output_tokens = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or fallback_output
        or 1
    )
    cached_tokens = int(
        usage.get("cached_tokens", 0)
        or usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    )

    resolved_model = (
        model_name
        or getattr(response, "response_metadata", {}).get("model_name")
        or DEFAULT_MODEL
    )

    return {
        "name": step_name,
        "model": str(resolved_model),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cached_tokens": cached_tokens,
        "latency_ms": round(latency_ms, 2),
    }


def aggregate_turn_metrics(
    call_metrics: list[CallMetrics] | None,
    start_time: float,
    messages: list[Any] | None = None,
) -> TurnMetrics:
    """Aggregate per-call metrics into a cohesive turn-level metrics object."""
    elapsed_s = round(time.perf_counter() - start_time, 2)
    metrics_list = list(call_metrics or [])

    # If no metrics were captured during graph run, synthesize from final state
    if not metrics_list and messages:
        input_tok = max(
            1,
            sum(
                estimate_tokens(getattr(m, "content", ""))
                for m in messages[:-1]
            ),
        )
        output_tok = max(1, estimate_tokens(getattr(messages[-1], "content", "")))
        metrics_list = [
            {
                "name": "Domain Agent (Call #2)",
                "model": DEFAULT_MODEL,
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "total_tokens": input_tok + output_tok,
                "cached_tokens": 0,
                "latency_ms": round(elapsed_s * 1000, 2),
            }
        ]

    total_input = sum(m.get("input_tokens", 0) for m in metrics_list)
    total_output = sum(m.get("output_tokens", 0) for m in metrics_list)
    total_cached = sum(m.get("cached_tokens", 0) for m in metrics_list)
    total_tokens = total_input + total_output

    active_model = (
        metrics_list[-1].get("model", DEFAULT_MODEL)
        if metrics_list
        else DEFAULT_MODEL
    )
    context_used_pct = round((total_input / CONTEXT_WINDOW_LIMIT) * 100, 2)

    return {
        "latency_s": elapsed_s,
        "total_tokens": total_tokens,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cached_tokens": total_cached,
        "context_limit": CONTEXT_WINDOW_LIMIT,
        "context_used_pct": context_used_pct,
        "llm_calls": len(metrics_list),
        "model": active_model,
        "breakdown": metrics_list,
    }
