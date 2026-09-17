def extract_text_content(message_content):
    """Extract string content regardless of provider format."""
    if isinstance(message_content, str):
        return message_content
    elif isinstance(message_content, list):
        # Extract text from block lists returned by Gemini/Claude/LangChain
        text_parts = []
        for block in message_content:
            if isinstance(block, dict):
                text_parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                text_parts.append(str(block))
        return " ".join(text_parts)
    return message_content
