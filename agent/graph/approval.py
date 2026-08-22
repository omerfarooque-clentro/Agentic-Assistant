from langgraph.types import interrupt
from langchain_core.messages import AIMessage


# Tool names that must be gated behind human approval before execution, grouped by domain.
EMAIL_APPROVAL_TOOL_NAMES = {"send_gmail_message", "draft_gmail_message"}
CALENDAR_APPROVAL_TOOL_NAMES = {"manage_event", "manage_out_of_office", "manage_focus_time", "create_calendar"}
DOCS_APPROVAL_TOOL_NAMES = {
    "create_doc", "modify_doc_text", "find_and_replace_doc", "insert_doc_elements",
    "insert_doc_image", "update_doc_headers_footers", "batch_update_doc",
    "create_table_with_data", "update_paragraph_style", "manage_doc_tab",
}
SHEETS_APPROVAL_TOOL_NAMES = {"create_spreadsheet", "create_sheet", "modify_sheet_values", "append_table_rows"}
SLACK_APPROVAL_TOOL_NAMES = {"send_slack_message", "send_slack_dm", "create_slack_draft"}

TOOL_NAMES_BY_DOMAIN = {
    "email": EMAIL_APPROVAL_TOOL_NAMES,
    "calendar": CALENDAR_APPROVAL_TOOL_NAMES,
    "docs": DOCS_APPROVAL_TOOL_NAMES,
    "sheets": SHEETS_APPROVAL_TOOL_NAMES,
    "slack": SLACK_APPROVAL_TOOL_NAMES,
}

# tool_name -> owning domain, used to route back to the right *_tools node after approval.
DOMAIN_BY_TOOL_NAME = {
    tool_name: domain
    for domain, tool_names in TOOL_NAMES_BY_DOMAIN.items()
    for tool_name in tool_names
}

APPROVAL_TOOL_NAMES = set(DOMAIN_BY_TOOL_NAME)

# Thread-safe duplicate check using unique tool_call_id
sent_tool_call_ids = set()

def approval_node(state):
    last_message = state["messages"][-1]

    # 1. Extract pending tool call safely, regardless of domain
    tool_calls = getattr(last_message, "tool_calls", [])
    tool_call = next(
        (call for call in tool_calls if call["name"] in APPROVAL_TOOL_NAMES),
        None,
    )
    if tool_call is None:
        raise ValueError("No pending approval tool call found.")

    tool_id = tool_call["id"]
    tool_name = tool_call["name"]
    args = tool_call.get("args", {})
    domain = DOMAIN_BY_TOOL_NAME[tool_name]

    # Check if this specific tool_call_id was already processed
    is_duplicate = tool_id in sent_tool_call_ids

    # 2. INTERRUPT (Pure read-only operation before this point)
    decision = interrupt({
        "type": "approval",
        "domain": domain,
        "tool_name": tool_name,
        "args": args,
        "is_duplicate": is_duplicate,
        "message": "This action was already executed. Re-run it?" if is_duplicate else f"Approve this {domain} action ({tool_name})?",
    })

    # 3. RESUME EXECUTION (Runs ONLY after user resumes)
    is_approved = decision.get("approved", False) if isinstance(decision, dict) else bool(decision)

    if is_approved:
        sent_tool_call_ids.add(tool_id)

    decision_details = decision if isinstance(decision, dict) else {}
    state_update = {
        "approved": is_approved,
        "details": {**decision_details, "domain": domain},
        "is_re_send": is_duplicate and is_approved,
    }

    # Keep the original tool-call AI message as the latest message on approval,
    # otherwise ToolNode has no pending tool call to execute.
    if not is_approved:
        state_update["messages"] = [
            AIMessage(
                content=f"Human approval rejected for {domain} action '{tool_name}'."
            )
        ]

    return state_update


def approval_result(state):
    if not state["approved"]:
        return "cancel"

    domain = state["details"]["domain"]
    return domain
