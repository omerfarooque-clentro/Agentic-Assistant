import logging
from typing import Any
from langchain_core.messages import HumanMessage, ToolMessage

from .registry import resolve_builder
from .schemas import ResultCardEnvelope

logger = logging.getLogger(__name__)

QUESTION_STARTERS = {
    "is", "are", "did", "does", "do", "any", "has", "have",
    "who", "what", "when", "where", "was", "were", "can", "could",
    "should", "would", "how", "why", "which"
}


def is_question(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if stripped.endswith("?"):
        return True
    first_word = stripped.split()[0].lower() if stripped.split() else ""
    return first_word in QUESTION_STARTERS


def get_presentation(user_text: str) -> dict[str, Any]:
    order = "text_first" if is_question(user_text) else "card_first"
    return {"order": order, "max_rows": 3}


def build_result_card(messages: list[Any]) -> dict | None:
    """Build a typed result card from the last successful tool message in the current turn."""
    if not messages:
        return None

    try:
        # 1. Identify the last HumanMessage to isolate the current turn
        last_human_idx = -1
        user_text = ""
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if isinstance(msg, HumanMessage) or getattr(msg, "type", None) == "human":
                last_human_idx = i
                content = getattr(msg, "content", "")
                user_text = content if isinstance(content, str) else str(content)
                break

        current_turn_messages = messages[last_human_idx + 1 :] if last_human_idx >= 0 else messages

        # 2. Scan backwards for candidate ToolMessage
        for msg in reversed(current_turn_messages):
            if not (isinstance(msg, ToolMessage) or getattr(msg, "type", None) == "tool"):
                continue

            tool_name = getattr(msg, "name", "")
            if not tool_name:
                continue

            # Check if this tool is in our registry
            resolved = resolve_builder(tool_name)
            if not resolved:
                continue

            # Check if tool run was an error
            status = getattr(msg, "status", None)
            if status == "error":
                continue

            tool_content = getattr(msg, "content", "")
            if isinstance(tool_content, str) and (
                tool_content.startswith("Error:") or "failed with error" in tool_content.lower()
            ):
                continue

            card_type, builder_fn = resolved
            card_data = builder_fn(tool_content)
            if not card_data:
                continue

            if card_type == "calendar_event" and isinstance(card_data, dict) and len(card_data.get("events", [])) > 1:
                card_type = "calendar_list"

            presentation = get_presentation(user_text)

            envelope_dict = {
                "kind": "result",
                "type": card_type,
                "v": 1,
                "presentation": presentation,
                "data": card_data,
            }

            envelope = ResultCardEnvelope.model_validate(envelope_dict)
            return envelope.model_dump()

        return None
    except Exception as e:
        logger.exception("build_result_card encountered unexpected exception: %s", e)
        return None
