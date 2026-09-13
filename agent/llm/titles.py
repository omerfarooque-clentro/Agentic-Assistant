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
