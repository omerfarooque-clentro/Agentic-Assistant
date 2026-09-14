from typing import TYPE_CHECKING, Any
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from .prompts import get_system_prompt
from .titles import TITLE_INSTRUCTION
if TYPE_CHECKING:
    from agent.graph.state import AgentState


RECENT_CONTEXT_MESSAGES = 4
MAX_TOOL_MESSAGE_CHARS = 3000


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


def messages_for_llm(state: AgentState, domain: str | None = None):
    """Assemble domain-scoped prompt and cross-domain conversational history."""
    messages = state.get("messages", [])
    target_domain = domain or state.get("domain")
    available_domains = state.get("available_domains")
    system_prompt = get_system_prompt(target_domain, available_domains=available_domains)
    if state.get("needs_title"):
        system_prompt = f"{system_prompt}{TITLE_INSTRUCTION}"

    plan = state.get("plan", [])
    if plan and any(isinstance(s, dict) and s.get("status") in ("in_progress", "pending") for s in plan):
        plan_summary = "\n".join(
            f"- Step {s.get('id', i+1)}: [{s.get('domain')}] {s.get('description')} (status: {s.get('status')})"
            for i, s in enumerate(plan)
        )
        system_prompt = (
            f"{system_prompt}\n\n"
            f"ACTIVE WORKFLOW PLAN:\n{plan_summary}\n"
            f"You are executing the step for domain '{target_domain}'. "
            "Use context, links, IDs, and outputs from previous steps in this conversation to perform your action."
        )

    latest_human_index = max(
        (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=0,
    )

    # Carry over clean conversational turns across domains (excluding raw tool payloads)
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

    return [SystemMessage(content=system_prompt), *previous_messages, *current_turn]
