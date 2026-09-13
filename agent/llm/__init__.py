from agent.llm.client import llm, llm_fast, bind_tools_with_fallback
from agent.llm.titles import (
    TITLE_INSTRUCTION,
    StreamTitleFilter,
    extract_title_from_text,
    generate_title_from_context,
    clean_heuristic_title,
)

