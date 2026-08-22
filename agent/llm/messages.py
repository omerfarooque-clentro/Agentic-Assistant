from agent.graph.state import AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from .prompts import SYSTEM_PROMPT


RECENT_CONTEXT_MESSAGES = 4
MAX_TOOL_MESSAGE_CHARS = 4000


def _truncate_tool_message(message):
    content = message.content
    if not isinstance(content, str) or len(content) <= MAX_TOOL_MESSAGE_CHARS:
        return message

    head_size = MAX_TOOL_MESSAGE_CHARS // 2
    tail_size = MAX_TOOL_MESSAGE_CHARS - head_size
    truncated_content = (
        f"{content[:head_size]}\n"
        "[Tool output truncated for context]\n"
        f"{content[-tail_size:]}"
    )
    return message.model_copy(update={"content": truncated_content})


def messages_for_llm(state: AgentState):
    messages = state["messages"]
    latest_human_index = max(
        (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=0,
    )

    previous_messages = [
        message
        for message in messages[:latest_human_index]
        if not isinstance(message, ToolMessage)
        and not (isinstance(message, AIMessage) and message.tool_calls)
    ][-RECENT_CONTEXT_MESSAGES:]
    current_turn = [
        _truncate_tool_message(message)
        for message in messages[latest_human_index:]
    ]

    return [SystemMessage(content=SYSTEM_PROMPT), *previous_messages, *current_turn]
