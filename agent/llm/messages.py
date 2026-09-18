from typing import TYPE_CHECKING, Any
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from agent.tools.domain_registry import TOOL_NAME_TO_DOMAIN
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
    has_active_plan = bool(
        plan
        and any(
            isinstance(s, dict) and s.get("status") in ("in_progress", "pending")
            for s in plan
        )
    )

    if has_active_plan:
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

    completed_actions = state.get("completed_actions") or []
    valid_actions = [
        a for a in completed_actions
        if isinstance(a, dict) and a.get("summary") and not a.get("__reset__")
    ]
    if valid_actions:
        actions_text = "\n".join(
            f"- [{a.get('domain', '').upper()}]: {a.get('summary', '').strip()}"
            for a in valid_actions
        )
        system_prompt = (
            f"{system_prompt}\n\n"
            f"PRIOR COMPLETED ACTIONS IN THIS WORKFLOW:\n{actions_text}\n"
            "Take into account the actions already completed above. Use any links/data produced. "
            "Do not duplicate completed actions unless explicitly requested."
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

    raw_current = [
        _truncate_tool_message(message)
        for message in messages[latest_human_index:]
    ]

    if has_active_plan and len(raw_current) > 1:
        # In a multi-step plan, only keep tool interactions belonging to the current domain.
        # Prior domain results are already cleanly provided in PRIOR COMPLETED ACTIONS above.
        current_turn = [raw_current[0]]  # Always include the HumanMessage
        for msg in raw_current[1:]:
            if isinstance(msg, ToolMessage):
                tool_name = getattr(msg, "name", "")
                tool_dom = (
                    TOOL_NAME_TO_DOMAIN.get(tool_name)
                    or ("slack" if tool_name.startswith("slack_") or tool_name == "resolve_slack_id" else None)
                    or ("research" if tool_name.startswith("tavily_") else None)
                )
                if tool_dom == target_domain:
                    current_turn.append(msg)
            elif isinstance(msg, AIMessage):
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    tc_name = tool_calls[0].get("name", "") if tool_calls else ""
                    tc_dom = (
                        TOOL_NAME_TO_DOMAIN.get(tc_name)
                        or ("slack" if tc_name.startswith("slack_") or tc_name == "resolve_slack_id" else None)
                        or ("research" if tc_name.startswith("tavily_") else None)
                    )
                    if tc_dom == target_domain:
                        current_turn.append(msg)
    else:
        current_turn = raw_current

    return [SystemMessage(content=system_prompt), *previous_messages, *current_turn]
