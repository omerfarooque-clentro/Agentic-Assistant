"""Metrics types and configuration constants."""

from __future__ import annotations
from typing import TypedDict


CONTEXT_WINDOW_LIMIT: int = 128_000
DEFAULT_MODEL: str = "openai/gpt-oss-120b"
DEFAULT_FAST_MODEL: str = "llama-3.1-8b-instant"


class CallMetrics(TypedDict, total=False):
    name: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int
    latency_ms: float


class TurnMetrics(TypedDict, total=False):
    latency_s: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    context_limit: int
    context_used_pct: float
    llm_calls: int
    model: str
    breakdown: list[CallMetrics]
