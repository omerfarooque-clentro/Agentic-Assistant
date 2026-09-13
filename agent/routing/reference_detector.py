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

EXPLICIT_DOMAIN_KEYWORDS = {
    "slack", "mail", "email", "emails", "gmail", "calendar", "cal", "meeting", "meetings", "event", "events",
    "doc", "docs", "document", "documents", "sheet", "sheets", "spreadsheet", "spreadsheets",
    "web", "search web", "web search", "wb saerch", "google", "tavily", "internet", "browse",
}

AMBIGUOUS_ACTION_TERMS = {
    "message", "messages", "msg", "msgs", "meesage", "meesages", "chat", "chats", "dm", "dms", "unread", "latest from", "recent from",
}


ENVELOPE_PATTERN = re.compile(
    r"^Date:\s*[\d\-:\s]+(?:\(Timezone:[^)]+\))?,\s*[^:]+:\s*",
    re.IGNORECASE,
)


def strip_message_envelope(text: str) -> str:
    """Strip 'Date: ... (Timezone: ...), <User>: ' envelope from formatted prompt text."""
    if not text or not isinstance(text, str):
        return text or ""
    return ENVELOPE_PATTERN.sub("", text).strip()


def extract_message_text(message: Any, strip_envelope: bool = False) -> str:
    """Normalize and extract plain text from any LangChain message, dict, string, or object."""
    if message is None:
        return ""
    if isinstance(message, str):
        raw = message
    elif isinstance(message, dict):
        raw = str(message.get("text") or message.get("content") or "")
    elif hasattr(message, "content"):
        content = message.content
        if isinstance(content, str):
            raw = content
        elif isinstance(content, list):
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            raw = " ".join(parts)
        else:
            raw = str(content)
    else:
        raw = str(message)

    return strip_message_envelope(raw) if strip_envelope else raw


def has_conversational_reference(text: str) -> bool:
    """Check whether a user message contains pronouns or references requiring context resolution."""
    if not text or not isinstance(text, str):
        return False

    cleaned = strip_message_envelope(text).strip().lower()
    words = cleaned.split()

    # Check for short follow-up commands like "send it", "email that", "do it"
    if len(words) <= 4 and any(trigger in words for trigger in SHORT_ACTION_TRIGGERS):
        return True

    return bool(REFERENCE_PATTERN.search(cleaned))


def is_domain_ambiguous(text: str) -> bool:
    """Detect if a query lacks an explicit service keyword and uses ambiguous communication terms (e.g. 'check latest message from arsalan')."""
    if not text or not isinstance(text, str):
        return False
    cleaned = strip_message_envelope(text).strip().lower()

    # Check if any explicit service keyword is present
    for kw in EXPLICIT_DOMAIN_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", cleaned):
            return False

    # Check if any ambiguous action term is present
    for term in AMBIGUOUS_ACTION_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", cleaned):
            return True

    return False

