"""Rule-based reference and anaphora detection for zero-cost routing decisions."""

from __future__ import annotations

import re
from typing import Any

# Fast regex pattern to detect pronouns and anaphoric references indicating multi-turn context
REFERENCE_PATTERN = re.compile(
    r"\b(it|that|this|them|those|these|him|her|his|hers|their|the same|same|previous|prior|earlier|above|again|instead|also|too|latter|former)\b",
    re.IGNORECASE,
)

# Short imperative follow-up triggers that depend on previous assistant context
SHORT_ACTION_TRIGGERS = {"yes", "send", "email", "share", "post", "cancel", "confirm", "reply", "forward"}


def extract_message_text(message: Any) -> str:
    """Normalize and extract plain text from any LangChain message, dict, string, or object."""
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return str(message.get("text") or message.get("content") or "")
    if hasattr(message, "content"):
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return " ".join(parts)
        return str(content)
    return str(message)


def has_conversational_reference(text: str) -> bool:
    """Check whether a user message contains pronouns or references requiring context resolution."""
    if not text or not isinstance(text, str):
        return False

    cleaned = text.strip().lower()
    words = cleaned.split()

    # Check for short follow-up commands like "send it", "email that", "do it"
    if len(words) <= 4 and any(trigger in words for trigger in SHORT_ACTION_TRIGGERS):
        return True

    return bool(REFERENCE_PATTERN.search(cleaned))
