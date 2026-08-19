from langgraph.types import interrupt

# Tool names that must be gated behind human approval before execution.
EMAIL_APPROVAL_TOOL_NAMES = {"send_email", "send_message", "create_draft"}
SLACK_APPROVAL_TOOL_NAMES = {"send_slack_message", "send_slack_dm", "create_slack_draft"}

# Thread-safe duplicate check using unique tool_call_id
sent_tool_call_ids = set()

def email_approval(state):
    print("[NODE:email_approval] Pending email action, preparing HITL interrupt...")
    last_message = state["messages"][-1]

    # 1. Extract pending tool call safely
    tool_calls = getattr(last_message, "tool_calls", [])
    tool_call = next(
        (call for call in tool_calls if call["name"] in EMAIL_APPROVAL_TOOL_NAMES),
        None,
    )
    if tool_call is None:
        raise ValueError("No pending email approval tool call found.")

    tool_id = tool_call["id"]
    tool_name = tool_call["name"]
    args = tool_call.get("args", {})

    # Check if this specific tool_call_id was already processed
    is_duplicate = tool_id in sent_tool_call_ids

    # 2. INTERRUPT (Pure read-only operation before this point)
    print(f"[NODE:email_approval] Interrupting for user decision: to={args.get('to')} subject={args.get('subject')!r}")
    decision = interrupt({
        "type": "email_approval",
        "tool_name": tool_name,
        "to": args.get("to"),
        "subject": args.get("subject"),
        "body": args.get("body"),
        "is_duplicate": is_duplicate,
        "message": "This email was already sent/drafted. Re-send/re-draft?" if is_duplicate else "Approve sending/saving this email/drafting?",
    })

    # 3. RESUME EXECUTIONS (Runs ONLY after user resumes)
    is_approved = decision.get("approved", False) if isinstance(decision, dict) else bool(decision)
    print(f"[NODE:email_approval] Resumed with approved={is_approved}")

    if is_approved:
        print(is_approved)
        sent_tool_call_ids.add(tool_id)

    return {
        "email_approved": is_approved,
        "details" : {**decision},
        "is_re_send": is_duplicate and is_approved
    }


def approval_result(state):
    decision = "send" if state["email_approved"] else "cancel"
    print(f"[ROUTER:email_approval] approval_result -> '{decision}'")
    return decision