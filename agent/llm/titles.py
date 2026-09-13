"""Utilities for suggested thread title prompt instructions, token filtering, and extraction."""

import re

TITLE_INSTRUCTION = (
    "\n\nTHREAD NAMING RULE:\n"
    "This is a new conversation thread. In your final response to the user (when not calling tools), "
    "conclude your message with: <suggested_title>Concise 3-5 Word Title</suggested_title>."
)


class StreamTitleFilter:
    """Filters out <suggested_title>...</suggested_title> tags from live token streams

    so the user never sees raw tag markup in the chat window, while extracting the
    underlying suggested title string.
    """

    TAG_START = "<suggested_title>"
    TAG_END = "</suggested_title>"

    def __init__(self):
        self.buffer = ""
        self.in_tag = False
        self.tag_content = []
        self.extracted_title = None

    def process_chunk(self, chunk: str) -> str:
        """Process an incoming streaming text chunk and return only user-facing tokens."""
        self.buffer += chunk
        output = []

        while self.buffer:
            if not self.in_tag:
                idx = self.buffer.find("<")
                if idx == -1:
                    output.append(self.buffer)
                    self.buffer = ""
                    break
                else:
                    if idx > 0:
                        output.append(self.buffer[:idx])
                        self.buffer = self.buffer[idx:]

                    buf_lower = self.buffer.lower()
                    if self.TAG_START.startswith(buf_lower):
                        if buf_lower == self.TAG_START:
                            self.in_tag = True
                            self.buffer = ""
                        # Partial match: wait for subsequent chunks
                        break
                    elif buf_lower.startswith(self.TAG_START):
                        self.in_tag = True
                        self.buffer = self.buffer[len(self.TAG_START):]
                    else:
                        output.append(self.buffer[0])
                        self.buffer = self.buffer[1:]
            else:
                buf_lower = self.buffer.lower()
                end_idx = buf_lower.find(self.TAG_END)
                if end_idx != -1:
                    self.tag_content.append(self.buffer[:end_idx])
                    self.buffer = self.buffer[end_idx + len(self.TAG_END):]
                    self.in_tag = False
                    self.extracted_title = "".join(self.tag_content).strip()
                else:
                    possible_partial = False
                    for i in range(1, len(self.TAG_END)):
                        if buf_lower.endswith(self.TAG_END[:i]):
                            self.tag_content.append(self.buffer[:-i])
                            self.buffer = self.buffer[-i:]
                            possible_partial = True
                            break
                    if not possible_partial:
                        self.tag_content.append(self.buffer)
                        self.buffer = ""
                    break

        return "".join(output)

    def finalize(self) -> tuple[str, str | None]:
        """Flush remaining buffer and return any trailing tokens and the final title."""
        remaining_output = ""
        if self.buffer:
            if not self.in_tag:
                remaining_output = self.buffer
            else:
                self.tag_content.append(self.buffer)
            self.buffer = ""

        if not self.extracted_title and self.tag_content:
            raw = "".join(self.tag_content).strip()
            clean = re.sub(r"</?suggested_title>?$", "", raw, flags=re.IGNORECASE).strip()
            if clean:
                self.extracted_title = clean

        title = self.extracted_title
        if title:
            title = re.sub(r"<[^>]+>", "", title).strip().strip("\"' ")[:60]
            self.extracted_title = title if title else None

        return remaining_output, self.extracted_title


def extract_title_from_text(text: str) -> tuple[str, str | None]:
    """Extract <suggested_title> from complete message content and return clean text."""
    if not text or not isinstance(text, str):
        return text or "", None

    match = re.search(r"<suggested_title>(.*?)(?:</suggested_title>|$)", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip()
        title = re.sub(r"<[^>]+>", "", title).strip().strip("\"' ")[:60]
        cleaned_text = (text[:match.start()] + text[match.end():]).strip()
        return cleaned_text, title if title else None

    return text, None


def clean_heuristic_title(text: str) -> str:
    """Generate a clean 3-6 word title from a user query string when LLM is unavailable."""
    if not text:
        return "New Conversation"
    # Remove dates/timestamp formatting if present (e.g. Date: ... Omer: ...)
    clean = re.sub(r"^Date:[^,]+,\s*[^:]+:\s*", "", text, flags=re.IGNORECASE).strip()
    # Strip common leading question / polite phrases
    prefixes = [
        r"^(can\s+you\s+(please\s+)?(help\s+me\s+)?(to\s+)?)",
        r"^(could\s+you\s+(please\s+)?)",
        r"^(please\s+)",
        r"^(tell\s+me\s+(about\s+)?)",
        r"^(what\s+is\s+(the\s+)?)",
        r"^(what\s+are\s+(the\s+)?)",
        r"^(who\s+is\s+(the\s+)?)",
        r"^(who\s+won\s+(the\s+)?)",
        r"^(how\s+do\s+i\s+)",
        r"^(how\s+to\s+)",
        r"^(where\s+is\s+)",
        r"^(search\s+(for\s+)?)",
        r"^(find\s+(me\s+)?)",
        r"^(show\s+(me\s+)?)",
        r"^(check\s+(if\s+|for\s+)?)",
        r"^(lookup\s+)",
    ]
    for p in prefixes:
        clean = re.sub(p, "", clean, flags=re.IGNORECASE).strip()

    # Remove special characters / punctuation
    clean = re.sub(r"[^\w\s\-\/]", " ", clean).strip()
    words = clean.split()
    if not words:
        return "New Conversation"

    # Take first 3 to 6 words
    chosen_words = words[:6]
    title = " ".join(chosen_words)
    title = " ".join(w.capitalize() if not w.isupper() else w for w in title.split())
    return title[:50].strip() or "New Conversation"


async def generate_title_from_context(user_prompt: str, assistant_response: str = "") -> str:
    """Generate a concise 3-5 word conversation title using suggested tag, fast LLM, or heuristic fallback."""
    # 1. First check if assistant response contained <suggested_title>
    if assistant_response:
        _, extracted = extract_title_from_text(assistant_response)
        if extracted and extracted.lower() not in ("new thread", "new conversation"):
            return extracted

    # Clean the user prompt (remove metadata prefix like "Date: ... Omer: ")
    clean_prompt = re.sub(r"^Date:[^,]+,\s*[^:]+:\s*", "", user_prompt or "", flags=re.IGNORECASE).strip()
    if not clean_prompt:
        return "New Conversation"

    # 2. Try fast LLM invocation with a strict timeout
    try:
        import asyncio
        from langchain_core.messages import SystemMessage, HumanMessage
        from agent.llm.client import llm_fast

        prompt_excerpt = clean_prompt[:250]
        messages = [
            SystemMessage(
                content=(
                    "You are a conversation title generator. "
                    "Output ONLY a concise, high-quality 3 to 5 word title summarizing the user's initial message. "
                    "Do NOT include quotes, punctuation, prefixes, or formatting. Maximum 50 characters."
                )
            ),
            HumanMessage(content=prompt_excerpt),
        ]
        response = await asyncio.wait_for(llm_fast.ainvoke(messages), timeout=2.5)
        raw_text = response.content if hasattr(response, "content") else str(response)
        if isinstance(raw_text, list):
            raw_text = " ".join(str(b.get("text") or "") if isinstance(b, dict) else str(b) for b in raw_text)
        candidate = re.sub(r"<[^>]+>", "", str(raw_text)).strip().strip("\"' \n\r\t")
        candidate = candidate.rstrip(".!?:")
        if candidate and len(candidate.split()) >= 1 and candidate.lower() not in ("new thread", "new conversation"):
            return candidate[:50]
    except Exception:
        pass

    # 3. Robust heuristic fallback
    return clean_heuristic_title(clean_prompt)
