SERVICE_TO_DOMAIN = {
    "gmail": "email",
    "calendar": "calendar",
    "docs": "docs",
    "sheets": "sheets",
    "slack": "slack",
    "tavily": "research",
    "brave": "research",
}


# The Google Workspace MCP server may return tools across products in one response.
# Route tool ownership by tool-name allowlist when available.
GMAIL_TOOL_NAMES = {
    "search_gmail_messages", "get_gmail_message_content", "get_gmail_messages_content_batch",
    "get_gmail_attachment_content", "send_gmail_message", "draft_gmail_message",
    "get_gmail_thread_content", "get_gmail_threads_content_batch", "list_gmail_labels",
    "manage_gmail_label", "list_gmail_filters", "manage_gmail_filter",
    "modify_gmail_message_labels", "batch_modify_gmail_message_labels",
}
CALENDAR_TOOL_NAMES = {
    "list_calendars", "get_events", "manage_event", "manage_out_of_office",
    "manage_focus_time", "query_freebusy", "create_calendar",
}
DOCS_TOOL_NAMES = {
    "search_docs", "get_doc_content", "list_docs_in_folder", "create_doc",
    "modify_doc_text", "find_and_replace_doc", "insert_doc_elements", "insert_doc_image",
    "update_doc_headers_footers", "batch_update_doc", "inspect_doc_structure",
    "debug_docs_runtime_info", "create_table_with_data", "debug_table_structure",
    "export_doc_to_pdf", "update_paragraph_style", "get_doc_as_markdown", "manage_doc_tab",
    "list_document_comments", "manage_document_comment",
}
SHEETS_TOOL_NAMES = {
    "list_spreadsheets", "get_spreadsheet_info", "read_sheet_values", "modify_sheet_values",
    "format_sheet_range", "manage_conditional_formatting", "create_spreadsheet", "create_sheet",
    "list_sheet_tables", "append_table_rows", "resize_sheet_dimensions", "move_sheet_rows",
    "list_spreadsheet_comments", "manage_spreadsheet_comment",
}

DOMAIN_TOOL_NAMES = {
    "email": GMAIL_TOOL_NAMES,
    "calendar": CALENDAR_TOOL_NAMES,
    "docs": DOCS_TOOL_NAMES,
    "sheets": SHEETS_TOOL_NAMES,
}

TOOL_NAME_TO_DOMAIN = {
    tool_name: domain
    for domain, tool_names in DOMAIN_TOOL_NAMES.items()
    for tool_name in tool_names
}


def get_domain_for_service(service):
    return SERVICE_TO_DOMAIN.get(service, service)


def resolve_tool_domain(service, tool_name):
    fallback_domain = get_domain_for_service(service)
    return TOOL_NAME_TO_DOMAIN.get(tool_name, fallback_domain)
