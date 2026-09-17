from langchain_core.messages import ToolMessage
from agent.graph.approval import DOMAIN_BY_TOOL_NAME



def render_cards(approval, messages, modified_args=None, approved=True, instruction=None):
    card_domain = getattr(approval, "domain", "") or ""
    if not card_domain or card_domain == "general":
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                card_domain = DOMAIN_BY_TOOL_NAME.get(getattr(m, "name", ""), "")
                if card_domain:
                    break
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in reversed(m.tool_calls):
                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    card_domain = DOMAIN_BY_TOOL_NAME.get(tc_name, "")
                    if card_domain:
                        break
                if card_domain:
                    break
    if not card_domain:
        card_domain = "general"

    tool_args = dict(modified_args) if modified_args else {}
    if not tool_args:
        for m in reversed(messages):
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in reversed(m.tool_calls):
                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if DOMAIN_BY_TOOL_NAME.get(tc_name) == card_domain:
                        tool_args = tc.get("args") or {} if isinstance(tc, dict) else getattr(tc, "args", {})
                        break
                if tool_args:
                    break

    return {
        "domain": card_domain,
        "approved": approved,
        "status": "completed" if approved else ("revised" if instruction else "cancelled"),
        "heading": f"{card_domain.title()} action",
        "args": tool_args,
    }

