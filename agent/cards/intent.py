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
    if is_question(user_text):
        return {"order": "text_first", "max_rows": 3}
    return {"order": "card_first", "max_rows": 5}
