from typing import Any, Callable

from .builders import (
    build_calendar_event,
    build_email_list,
    build_search_summary,
    build_sheet_update,
    build_sheet_view,
    build_slack_mentions,
    build_weather,
)

# Map tool name -> (card_type, builder_function)
TOOL_REGISTRY: dict[str, tuple[str, Callable[[Any], dict | None]]] = {
    # Weather
    "get_weather": ("weather", build_weather),
    # Gmail
    "search_gmail_messages": ("email_list", build_email_list),
    "get_gmail_message_content": ("email_list", build_email_list),
    "get_gmail_messages_content_batch": ("email_list", build_email_list),
    "get_gmail_thread_content": ("email_list", build_email_list),
    "get_gmail_threads_content_batch": ("email_list", build_email_list),
    # Calendar
    "get_events": ("calendar_event", build_calendar_event),
    "manage_event": ("calendar_event", build_calendar_event),
    # Sheets
    "read_sheet_values": ("sheet_view", build_sheet_view),
    "get_spreadsheet_info": ("sheet_view", build_sheet_view),
    "list_spreadsheets": ("sheet_view", build_sheet_view),
    "modify_sheet_values": ("sheet_update", build_sheet_update),
    "append_table_rows": ("sheet_update", build_sheet_update),
    # Slack
    "slack_read_channel": ("slack_mentions", build_slack_mentions),
    "slack_read_thread": ("slack_mentions", build_slack_mentions),
    "slack_search_public_and_private": ("slack_mentions", build_slack_mentions),
    "slack_search_channels": ("slack_mentions", build_slack_mentions),
    # Tavily / Web
    "tavily_search": ("search_summary", build_search_summary),
    "tavily_search_advanced": ("search_summary", build_search_summary),
    "tavily_search_context": ("search_summary", build_search_summary),
    "tavily_qna_search": ("search_summary", build_search_summary),
    "search": ("search_summary", build_search_summary),
}


def resolve_builder(tool_name: str) -> tuple[str, Callable[[Any], dict | None]] | None:
    return TOOL_REGISTRY.get(tool_name)
