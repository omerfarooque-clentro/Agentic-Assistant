from langgraph.types import interrupt
 
# Thread-safe duplicate check using unique tool_call_id
sent_tool_call_ids = set()

def email_approval(state):
    last_message = state["messages"][-1]

    # 1. Extract pending tool call safely
    tool_calls = getattr(last_message, "tool_calls", [])
    tool_call = next(
        (call for call in tool_calls if call["name"] == "send_email"),
        None,
    )

    if tool_call is None:
        raise ValueError("No pending send_email tool call found.")

    tool_id = tool_call["id"]
    args = tool_call.get("args", {})

    # Check if this specific tool_call_id was already processed
    is_duplicate = tool_id in sent_tool_call_ids

    # 2. INTERRUPT (Pure read-only operation before this point)
    decision = interrupt({
        "type": "email_approval",
        "to": args.get("to"),
        "subject": args.get("subject"),
        "body": args.get("body"),
        "is_duplicate": is_duplicate,
        "message": "This email was already sent. Re-send?" if is_duplicate else "Approve sending this email?",
    })

    # 3. RESUME EXECUTIONS (Runs ONLY after user resumes)
    is_approved = decision.get("approved", False) if isinstance(decision, dict) else bool(decision)

    if is_approved:
        print(is_approved)
        sent_tool_call_ids.add(tool_id)

    return {
        "email_approved": is_approved,
        "details" : {**decision},
        "is_re_send": is_duplicate and is_approved
    }


def approval_result(state):
    print(f"Approval Result State: {state['email_approved']}")
    if state["email_approved"]:
        return "send"
    return "cancel"