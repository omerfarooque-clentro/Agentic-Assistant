NODE_STATUS_MAP = {
    "nlp": {
        "status": "thinking",
        "agent": "Planner",
        "message": "⚡ Personal Ops: Planning workflow…",
    },
    "advance_plan": {
        "status": "thinking",
        "agent": "Planner",
        "message": "⚡ Personal Ops: Coordinating next step…",
    },
    "general_agent": {
        "status": "thinking",
        "agent": "Personal Ops",
        "message": "⚡ Personal Ops: Formulating response…",
    },
    "research_agent": {
        "status": "searching",
        "agent": "Research Agent",
        "message": "🔍 Research Agent: Searching web sources…",
    },
    "email_agent": {
        "status": "tool_calling",
        "agent": "Email Agent",
        "message": "✉️ Email Agent: Processing Gmail action…",
    },
    "calendar_agent": {
        "status": "tool_calling",
        "agent": "Calendar Agent",
        "message": "📅 Calendar Agent: Managing schedule…",
    },
    "docs_agent": {
        "status": "tool_calling",
        "agent": "Docs Agent",
        "message": "📄 Docs Agent: Working on documents…",
    },
    "sheets_agent": {
        "status": "tool_calling",
        "agent": "Sheets Agent",
        "message": "📊 Sheets Agent: Inspecting spreadsheet…",
    },
    "slack_agent": {
        "status": "tool_calling",
        "agent": "Slack Agent",
        "message": "💬 Slack Agent: Interacting with Slack…",
    },
}