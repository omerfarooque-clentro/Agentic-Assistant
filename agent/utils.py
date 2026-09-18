"""Shared utility helpers for the agent package."""

from typing import Any
from langchain_core.messages import AIMessage, HumanMessage


def extract_text_content(message_content: Any) -> str:
    """Extract string content regardless of provider format."""
    if isinstance(message_content, str):
        return message_content
    elif isinstance(message_content, list):
        text_parts = []
        for block in message_content:
            if isinstance(block, dict):
                text_parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                text_parts.append(str(block))
        return " ".join(text_parts)
    return str(message_content) if message_content is not None else ""


def is_intermediate_plan_step(plan: Any) -> bool:
    """Check whether a workflow plan has pending steps that are yet to execute."""
    if not plan or not isinstance(plan, list) or len(plan) <= 1:
        return False
    return any(isinstance(s, dict) and s.get("status") == "pending" for s in plan)


def extract_turn_ai_messages(messages: list[Any]) -> list[AIMessage]:
    """Return all AIMessages produced in the current turn after the latest HumanMessage."""
    latest_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
        default=-1,
    )
    return [
        m for m in messages[latest_human_idx + 1:]
        if isinstance(m, AIMessage) and getattr(m, "content", None)
    ]


def extract_turn_response(messages: list[Any]) -> str:
    """Extract and combine the latest assistant message(s) for the current user turn."""
    turn_ai_messages = extract_turn_ai_messages(messages)
    if len(turn_ai_messages) > 1:
        return "\n\n".join(
            extract_text_content(m.content)
            for m in turn_ai_messages
            if extract_text_content(m.content).strip()
        )
    elif turn_ai_messages:
        return extract_text_content(turn_ai_messages[-1].content)
    elif messages:
        return extract_text_content(messages[-1].content)
    return ""


def synthesize_response_from_actions(
    state_or_result: dict[str, Any],
    messages: list[Any] | None = None,
    default: str = "I processed your request. Let me know if there's anything else you'd like to do!",
) -> str:
    """Construct a clean user response from structured completed_actions when LLM emits no final text."""
    completed_actions = [
        a for a in (state_or_result.get("completed_actions") or [])
        if isinstance(a, dict) and a.get("summary") and not a.get("__reset__")
    ]
    if completed_actions:
        return "\n\n".join(
            f"**{a.get('domain', '').capitalize()}:** {a.get('summary', '').strip()}"
            for a in completed_actions
        )
    if messages:
        tool_calls = getattr(messages[-1], "tool_calls", None)
        if tool_calls:
            tool_names = ", ".join(t.get("name", "tool") for t in tool_calls)
            return f"Operation completed successfully ({tool_names})."
    return default


def extract_stream_chunk_token(chunk: Any, title_filter: Any) -> str | None:
    """Extract and filter a user-facing token string from an LLM stream chunk."""
    chunk_content = getattr(chunk, "content", None)
    if not chunk_content or not isinstance(chunk_content, str):
        return None
    return title_filter.process_chunk(chunk_content)
