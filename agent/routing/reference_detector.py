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
SHORT_ACTION_TRIGGERS = {"yes", "send", "email", "mail", "share", "post", "cancel", "confirm", "reply", "forward", "dispatch"}

EXPLICIT_DOMAIN_KEYWORDS = {
    "slack", "mail", "mial", "email", "emails", "gmail", "calendar", "calander", "calender", "cal", "meeting", "meetings", "event", "events",
    "schedule", "interview", "appointment", "availability", "availbility", "freebusy",
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


AFFIRMATIVE_FOLLOWUPS = {
    "yes", "yep", "yeah", "ok", "okay", "sure", "sure go ahead", "go ahead",
    "do it", "confirm", "proceed", "approved", "looks good",
}


def has_conversational_reference(text: str) -> bool:
    """Check whether a user message contains pronouns or references requiring context resolution."""
    if not text or not isinstance(text, str):
        return False

    cleaned = strip_message_envelope(text).strip().lower()
    words = cleaned.split()

    # Check for short affirmative confirmations
    if len(words) <= 4:
        if cleaned in AFFIRMATIVE_FOLLOWUPS or any(term in cleaned for term in ["go ahead", "do it", "looks good"]):
            return True
        if any(w in {"yes", "yep", "yeah", "sure", "proceed", "confirm"} for w in words):
            return True

    # Check isolated directive from large payloads
    directive = extract_action_directive(text)
    if directive != text:
        if has_conversational_reference(directive):
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


DOMAIN_KEYWORD_MAP = {
    "slack": "slack",
    "mail": "email",
    "mial": "email",
    "email": "email",
    "emails": "email",
    "gmail": "email",
    "inbox": "email",
    "calendar": "calendar",
    "calander": "calendar",
    "calender": "calendar",
    "cal": "calendar",
    "meeting": "calendar",
    "meetings": "calendar",
    "schedule": "calendar",
    "scheduling": "calendar",
    "interview": "calendar",
    "appointment": "calendar",
    "availability": "calendar",
    "availbility": "calendar",
    "freebusy": "calendar",
    "event": "calendar",
    "events": "calendar",
    "doc": "docs",
    "docs": "docs",
    "document": "docs",
    "documents": "docs",
    "sheet": "sheets",
    "sheets": "sheets",
    "spreadsheet": "sheets",
    "spreadsheets": "sheets",
    "web": "research",
    "web search": "research",
    "search web": "research",
    "google": "research",
    "tavily": "research",
    "internet": "research",
}

POLITE_PREFIX_PATTERN = re.compile(
    r"^(?:hey\s+(?:assistant|bot|there)?[\s,]*|hi\s+(?:assistant|there)?[\s,]*|hello\s+(?:assistant|there)?[\s,]*|good\s+(?:morning|afternoon|evening)[\s,]*|please[\s,]*|can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?|would\s+you\s+(?:please\s+)?|i\s+need\s+you\s+to\s+|help\s+me\s+(?:to\s+)?)+",
    re.IGNORECASE,
)

EMAIL_ADDRESS_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

ACTION_VERB_PATTERN = re.compile(
    r"\b(send|dispatch|email|mail|forward|post|share|schedule|draft|create|search)\b",
    re.IGNORECASE,
)

CLOSING_DIRECTIVE_PATTERN = re.compile(
    r"(?:please\s+|now\s+|kindly\s+|also\s+|aslo\s+|and\s+)?\b(send|dispatch|email|mail|forward|post|share|schedule|draft)\b\s+(?:it|this|that|them|meesage|message|mail|draft)?\s*(?:to|on|via|in)?\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|#?[a-zA-Z0-9_\-\.]+)?",
    re.IGNORECASE,
)


def extract_action_directive(text: str) -> str:
    """Extract opening or closing action command from multi-line payloads (e.g. email drafts with closing directives)."""
    if not text or not isinstance(text, str):
        return text or ""

    cleaned = strip_message_envelope(text).strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return cleaned

    # Only attempt directive isolation if message is truly multi-line and large (> 100 chars)
    # A single-line user prompt must NEVER be truncated by directive slicing
    if len(lines) <= 1 or len(cleaned) < 100:
        return cleaned

    # 1. Check closing lines (tail) in reverse order (e.g. "... send it to shifa quick", "dispatch it")
    for line in reversed(lines[-3:]):
        match = CLOSING_DIRECTIVE_PATTERN.search(line)
        if match:
            # If the line itself is concise (< 90 chars), return the whole line
            if len(line) <= 90:
                return line
            # Otherwise extract the matching action phrase and any trailing context
            start_pos = match.start()
            return line[start_pos:].strip()

    # 2. Check opening lines (head) (e.g. "Send email to Shifa with the following draft:")
    for line in lines[:2]:
        match = CLOSING_DIRECTIVE_PATTERN.search(line)
        if match and len(line) <= 90:
            return line

    return cleaned


def detect_explicit_domains(text: str) -> set[str]:
    """Detect canonical service domains explicitly referenced in query text."""
    if not text or not isinstance(text, str):
        return set()
    cleaned = strip_message_envelope(text).strip().lower()

    matched = set()

    # Check for direct email addresses (e.g. user@domain.com)
    if EMAIL_ADDRESS_PATTERN.search(cleaned):
        matched.add("email")

    # Scan the full message text for any explicit domain keywords
    for kw, domain in DOMAIN_KEYWORD_MAP.items():
        if re.search(rf"\b{re.escape(kw)}\b", cleaned):
            matched.add(domain)

    return matched


def clean_conversational_prefix(text: str) -> str:
    """Strip leading conversational pleasantries before vectorization."""
    if not text or not isinstance(text, str):
        return text or ""
    cleaned = strip_message_envelope(text).strip()
    subbed = POLITE_PREFIX_PATTERN.sub("", cleaned).strip()
    # Don't strip if nothing substantial remains (e.g. query was just "hello" or "can you help me")
    return subbed if len(subbed) >= 3 else cleaned


def is_compound_multi_domain(text: str, available_domains: set[str] | None = None) -> bool:
    """Detect if a user message contains actions across multiple distinct service domains."""
    if not text or not isinstance(text, str):
        return False
    domains = detect_explicit_domains(text)
    if available_domains is not None:
        domains = domains.intersection(available_domains)
    if len(domains) >= 2:
        # Check for multi-action conjunctions/connectors or clause separators
        if re.search(r"\b(and|then|after|also|aslo|plus)\b|[,;\n]", text, re.IGNORECASE):
            return True
    return False



