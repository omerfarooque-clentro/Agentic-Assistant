import ast
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse
import dateutil.parser

from .schemas import (
    CalendarEventData,
    CalendarEventItem,
    EmailItem,
    EmailListData,
    SearchPoint,
    SearchSource,
    SearchSummaryData,
    SheetUpdateData,
    SheetViewData,
    SlackMentionItem,
    SlackMentionsData,
    WeatherData,
)

logger = logging.getLogger(__name__)

MEET_URL_PATTERN = re.compile(r"(?:Meeting|Google Meet|Conference):\s*(https?://[^\s\r\n]+)", re.IGNORECASE)

SOCIAL_OR_COMMENT_DOMAINS = {
    "facebook.com", "x.com", "twitter.com", "instagram.com",
    "reddit.com", "tiktok.com", "threads.net"
}


def _safe_load_payload(raw: Any) -> Any:
    """Safely convert raw string or structure to Python objects, unpacking MCP/LangChain content blocks."""
    if isinstance(raw, list):
        if raw and all(isinstance(item, dict) and "text" in item for item in raw):
            combined = "\n".join(item["text"] for item in raw if isinstance(item, dict) and "text" in item).strip()
            return _safe_load_payload(combined)
        return raw
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    raw_str = raw.strip()
    if not raw_str:
        return None
    try:
        loaded = json.loads(raw_str)
        if isinstance(loaded, (dict, list)):
            return _safe_load_payload(loaded)
    except Exception:
        pass
    try:
        if raw_str.startswith("{") or raw_str.startswith("["):
            loaded = ast.literal_eval(raw_str)
            if isinstance(loaded, (dict, list)):
                return _safe_load_payload(loaded)
    except Exception:
        pass
    return raw_str


def _validate_meet_url(url: str | None) -> str | None:
    """Accept only https URLs whose host is meet.google.com, zoom.us, or teams.microsoft.com."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return None
        host = (parsed.hostname or "").lower()
        if (
            host == "meet.google.com"
            or host == "zoom.us"
            or host.endswith(".zoom.us")
            or host == "teams.microsoft.com"
        ):
            return url
    except Exception:
        return None
    return None


def _clean_iso_datetime(raw: str) -> str:
    """Clean date/time string into ISO 8601, removing bracketed notes like '[weekday: Wednesday; ISO weekday: 3]'."""
    if not raw or not isinstance(raw, str):
        return ""
    cleaned = re.sub(r"\[.*?\]", "", raw)
    cleaned = re.sub(r"\(.*?\)", "", cleaned).strip()
    if not cleaned:
        return ""
    # If range like "2026-03-23T15:00:00 to 2026-03-23T16:00:00"
    if " to " in cleaned:
        parts = cleaned.split(" to ")
        return f"{_clean_iso_datetime(parts[0])} to {_clean_iso_datetime(parts[1])}"
    try:
        dt = dateutil.parser.parse(cleaned)
        return dt.isoformat()
    except Exception:
        return cleaned


def build_weather(tool_result: Any) -> dict | None:
    try:
        data = _safe_load_payload(tool_result)
        if not isinstance(data, dict):
            return None
        if data.get("error") or data.get("needs_clarification"):
            return None
        if "current" not in data or "days" not in data:
            return None
        validated = WeatherData.model_validate(data)
        return validated.model_dump()
    except Exception as e:
        logger.debug("Failed building weather card: %s", e)
        return None


def _parse_email_address(raw: str) -> tuple[str, str]:
    """Parse 'Sender Name <sender@example.com>' or 'sender@example.com'."""
    if not raw or raw.strip().lower() in ("(unknown sender)", "unknown"):
        return "Unknown", ""
    match = re.match(r"^(.*?)\s*<([^>]+)>$", raw.strip())
    if match:
        name = match.group(1).strip().strip('"\'')
        email = match.group(2).strip()
        return name or email, email
    clean = raw.strip().strip('"\'')
    if "@" in clean:
        parts = clean.split("@")
        name = parts[0].replace(".", " ").replace("_", " ").title()
        return name or clean, clean
    return clean, clean


def build_email_list(tool_result: Any, query: str = "") -> dict | None:
    try:
        payload = _safe_load_payload(tool_result)
        items: list[EmailItem] = []

        if isinstance(payload, dict) and ("messages" in payload or "items" in payload):
            raw_items = payload.get("messages") or payload.get("items") or []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                sender_raw = item.get("from") or item.get("sender") or "Unknown"
                name, email = _parse_email_address(sender_raw)
                msg_id = str(item.get("id") or item.get("message_id") or "")
                raw_date = str(item.get("date") or item.get("received_at") or "")
                received_at = _clean_iso_datetime(raw_date) if raw_date else ""
                items.append(
                    EmailItem(
                        id=msg_id,
                        sender_name=name,
                        sender_email=email,
                        subject=item.get("subject") or "(No Subject)",
                        snippet=item.get("snippet") or item.get("body", "")[:150],
                        received_at=received_at,
                        unread=bool(item.get("unread", False)),
                        thread_url=item.get("thread_url") or (f"https://mail.google.com/mail/u/0/#inbox/{msg_id}" if msg_id else None),
                    )
                )
        elif isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                # If this is an unhandled raw content block or dict without sender
                if "type" in item and "text" in item and len(item) == 2:
                    continue
                sender_raw = item.get("from") or item.get("sender") or "Unknown"
                name, email = _parse_email_address(sender_raw)
                msg_id = str(item.get("id") or item.get("message_id") or "")
                raw_date = str(item.get("date") or item.get("received_at") or "")
                received_at = _clean_iso_datetime(raw_date) if raw_date else ""
                items.append(
                    EmailItem(
                        id=msg_id,
                        sender_name=name,
                        sender_email=email,
                        subject=item.get("subject") or "(No Subject)",
                        snippet=item.get("snippet") or item.get("body", "")[:150],
                        received_at=received_at,
                        unread=bool(item.get("unread", False)),
                        thread_url=item.get("thread_url") or (f"https://mail.google.com/mail/u/0/#inbox/{msg_id}" if msg_id else None),
                    )
                )
        elif isinstance(payload, str):
            # Parse text output from Gmail tools
            id_matches = list(re.finditer(r"(?:^\s*(?:\d+\.\s*)?Message ID:\s*([^\r\n]+))", payload, re.MULTILINE))
            for idx, match in enumerate(id_matches):
                msg_id = match.group(1).strip()
                start = match.end()
                end = id_matches[idx + 1].start() if idx + 1 < len(id_matches) else len(payload)
                block = payload[start:end]

                subj_m = re.search(r"Subject:\s*([^\r\n]+)", block)
                from_m = re.search(r"From:\s*([^\r\n]+)", block)
                date_m = re.search(r"Date:\s*([^\r\n]+)", block)
                snip_m = re.search(r"(?:Snippet|Body):\s*([^\r\n]+)", block)
                link_m = re.search(r"(?:Web|Thread) Link:\s*([^\r\n]+)", block)

                sender_raw = from_m.group(1).strip() if from_m else "Unknown"
                name, email = _parse_email_address(sender_raw)
                raw_subj = subj_m.group(1).strip() if subj_m else "(No Subject)"
                subject = "(No Subject)" if raw_subj.lower() == "(no subject)" else raw_subj
                raw_date = date_m.group(1).strip() if date_m and "(unknown date)" not in date_m.group(1) else ""
                received_at = _clean_iso_datetime(raw_date) if raw_date else ""
                snippet = snip_m.group(1).strip() if snip_m else ""
                thread_url = link_m.group(1).strip() if link_m and link_m.group(1).strip() != "N/A" else f"https://mail.google.com/mail/u/0/#inbox/{msg_id}"

                items.append(
                    EmailItem(
                        id=msg_id,
                        sender_name=name,
                        sender_email=email,
                        subject=subject,
                        snippet=snippet,
                        received_at=received_at,
                        unread=False,
                        thread_url=thread_url,
                    )
                )

        if not items:
            return None

        unread = sum(1 for item in items if item.unread)
        data = EmailListData(
            query=query or "Recent Emails",
            total=len(items),
            unread=unread,
            items=items,
        )
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building email list card: %s", e)
        return None


def _resolve_slack_mention_id(slack_id: str) -> str:
    """Resolve a Slack user ID (e.g. U0A1MQ7FYEB) to display name from database cache, or return empty string."""
    try:
        from agent.models import SlackResource
        res = SlackResource.objects.filter(slack_id=slack_id).first()
        if res and res.name:
            return f"@{res.name.lstrip('@#').title()}"
    except Exception:
        pass
    return ""


def _clean_slack_channel(raw: str) -> str:
    """Clean raw channel string like 'DM (ID: D0BDRSYBQP8)' or '#general'."""
    if not raw:
        return "general"
    s = re.sub(r"\s*\(ID:\s*[^)]+\)", "", raw).strip()
    return s.lstrip("#") or "general"


def _clean_slack_sender(raw: str) -> str:
    """Clean raw sender string, stripping internal IDs and formatting friendly name."""
    if not raw:
        return "User"
    s = re.sub(r"\s*\(ID:\s*[^)]+\)", "", raw).strip()
    # Check if raw ID like <@U0A1MQ7FYEB> or U0A1MQ7FYEB
    mention_match = re.match(r"^<?@?([A-Z0-9]{8,14})>?$", s)
    if mention_match:
        resolved = _resolve_slack_mention_id(mention_match.group(1))
        if resolved:
            return resolved.lstrip("@")
        return "User"
    match = re.match(r"^(.*?)\s*<([^>]+)>$", s)
    if match:
        name = match.group(1).strip().strip('"')
        email = match.group(2).strip()
        return name or email
    return s or "User"


def _clean_slack_text(raw: str) -> str:
    """Clean message body, resolving mentions and stripping trailing Context after/before metadata blocks."""
    if not raw:
        return ""
    # Resolve user mentions like <@U0A1MQ7FYEB>
    cleaned = re.sub(
        r"<@([A-Z0-9]{8,14})>",
        lambda m: _resolve_slack_mention_id(m.group(1)) or "@user",
        raw,
    )
    cleaned = re.split(r"\bContext (?:after|before):\s*", cleaned, flags=re.IGNORECASE)[0]
    cleaned = re.sub(r"(?:\n|^)\s*---+\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*---+\s*$", "", cleaned)
    cleaned = re.sub(r"\bMessage_ts:\s*[\d.]+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _parse_slack_search_markdown(text: str) -> list[SlackMentionItem]:
    """Parse formatted slack search markdown containing ### Result X of Y blocks."""
    items: list[SlackMentionItem] = []
    sections = re.split(r"###\s*Result\s*\d+(?:\s*of\s*\d+)?", text)
    for section in sections:
        section = section.strip()
        if not section:
            continue
        channel_m = re.search(r"Channel:\s*([^\r\n]+)", section)
        from_m = re.search(r"From:\s*([^\r\n]+)", section)
        permalink_m = re.search(r"Permalink:\s*([^\r\n]+)", section)
        text_m = re.search(r"Text:\s*([\s\S]+)", section)

        channel = _clean_slack_channel(channel_m.group(1)) if channel_m else "general"
        sender = _clean_slack_sender(from_m.group(1)) if from_m else "User"
        permalink = permalink_m.group(1).strip() if permalink_m else None
        body = _clean_slack_text(text_m.group(1)) if text_m else ""

        if not body and not channel_m and not from_m:
            continue
        if not body:
            body = _clean_slack_text(section)

        items.append(
            SlackMentionItem(
                channel=channel,
                sender=sender,
                text=body,
                permalink=permalink,
            )
        )
    return items


def build_slack_mentions(tool_result: Any) -> dict | None:
    try:
        payload = _safe_load_payload(tool_result)
        items: list[SlackMentionItem] = []

        if isinstance(payload, dict):
            # Check if slack returned markdown under "results" key
            if "results" in payload and isinstance(payload["results"], str):
                items.extend(_parse_slack_search_markdown(payload["results"]))
            if not items:
                raw_list = payload.get("messages") or payload.get("items") or []
                for m in raw_list:
                    if not isinstance(m, dict):
                        continue
                    channel = _clean_slack_channel(m.get("channel") or m.get("channel_name") or "general")
                    sender = _clean_slack_sender(m.get("user") or m.get("sender") or m.get("username") or "User")
                    text = _clean_slack_text(m.get("text") or m.get("content") or "")
                    ts = str(m.get("ts") or m.get("timestamp") or "")
                    permalink = m.get("permalink")
                    if text:
                        items.append(
                            SlackMentionItem(
                                channel=channel,
                                sender=sender,
                                text=text,
                                ts=ts,
                                permalink=permalink,
                            )
                        )
        elif isinstance(payload, list):
            for m in payload:
                if not isinstance(m, dict):
                    continue
                channel = _clean_slack_channel(m.get("channel") or m.get("channel_name") or "general")
                sender = _clean_slack_sender(m.get("user") or m.get("sender") or m.get("username") or "User")
                text = _clean_slack_text(m.get("text") or m.get("content") or "")
                ts = str(m.get("ts") or m.get("timestamp") or "")
                permalink = m.get("permalink")
                if text:
                    items.append(
                        SlackMentionItem(
                            channel=channel,
                            sender=sender,
                            text=text,
                            ts=ts,
                            permalink=permalink,
                        )
                    )
        elif isinstance(payload, str):
            items = _parse_slack_search_markdown(payload)
            if not items:
                # Parse text lines if plain text returned
                lines = payload.splitlines()
                for line in lines:
                    if "#" in line and ":" in line:
                        match = re.search(r"#([A-Za-z0-9_-]+)\s*\|\s*([^:]+):\s*(.*)", line)
                        if match:
                            items.append(
                                SlackMentionItem(
                                    channel=_clean_slack_channel(match.group(1)),
                                    sender=_clean_slack_sender(match.group(2)),
                                    text=_clean_slack_text(match.group(3)),
                                    ts="",
                                )
                            )

        if not items:
            return None

        data = SlackMentionsData(items=items)
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building slack mentions card: %s", e)
        return None


def build_sheet_view(tool_result: Any) -> dict | None:
    try:
        payload = _safe_load_payload(tool_result)
        title = "Spreadsheet View"
        columns: list[str] = []
        rows: list[list[Any]] = []
        url: str | None = None

        if isinstance(payload, dict):
            title = payload.get("title") or payload.get("range") or title
            raw_values = payload.get("values") or payload.get("rows") or []
            url = payload.get("url")
            if raw_values and isinstance(raw_values, list):
                columns = [str(c) for c in raw_values[0]]
                rows = raw_values[1:] if len(raw_values) > 1 else []
        elif isinstance(payload, str):
            # Parse `Successfully read X rows from range 'Sheet1!A1:D5' in spreadsheet abc123`
            range_m = re.search(r"range '([^']+)'", payload)
            if range_m:
                title = range_m.group(1)
            sheet_id_m = re.search(r"spreadsheet ([A-Za-z0-9_-]+)", payload)
            if sheet_id_m:
                url = f"https://docs.google.com/spreadsheets/d/{sheet_id_m.group(1)}/edit"

            parsed_rows = []
            for line in payload.splitlines():
                line = line.strip()
                if line.startswith("Row") and ":" in line:
                    val_str = line.split(":", 1)[1].strip()
                    try:
                        parsed_row = ast.literal_eval(val_str)
                        if isinstance(parsed_row, list):
                            parsed_rows.append(parsed_row)
                    except Exception:
                        pass

            if parsed_rows:
                columns = [str(c) for c in parsed_rows[0]]
                rows = parsed_rows[1:] if len(parsed_rows) > 1 else []

        if not columns and not rows:
            return None

        data = SheetViewData(
            title=title,
            columns=columns,
            rows=rows,
            url=url,
        )
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building sheet view card: %s", e)
        return None


def build_sheet_update(tool_result: Any) -> dict | None:
    try:
        payload = _safe_load_payload(tool_result)
        title = "Sheet Update"
        columns: list[str] = []
        rows: list[list[Any]] = []
        row_number: int | None = None
        url: str | None = None

        if isinstance(payload, dict):
            title = payload.get("title") or title
            columns = payload.get("columns") or []
            rows = payload.get("rows") or []
            row_number = payload.get("row_number") or payload.get("highlight_row_index")
            url = payload.get("url")
        elif isinstance(payload, str):
            # Parse `Successfully updated range 'Sheet1!A4:C4' in spreadsheet abc123`
            # or `Successfully appended 1 row(s) to table 'Table1' in spreadsheet abc123`
            range_m = re.search(r"range '([^']+)'", payload)
            if range_m:
                title = range_m.group(1)
                # Try to extract row number from range (e.g., Sheet1!A4:C4 -> 4)
                range_part = range_m.group(1).split("!")[-1]
                row_m = re.search(r"[A-Za-z]+(\d+)", range_part)
                if row_m:
                    row_number = int(row_m.group(1))
            else:
                table_m = re.search(r"table '([^']+)'", payload)
                if table_m:
                    title = f"Table {table_m.group(1)}"

            sheet_id_m = re.search(r"spreadsheet ([A-Za-z0-9_-]+)", payload)
            if sheet_id_m:
                url = f"https://docs.google.com/spreadsheets/d/{sheet_id_m.group(1)}/edit"

        if not url and not row_number and "Successfully" not in str(payload):
            return None

        data = SheetUpdateData(
            title=title,
            columns=columns,
            rows=rows,
            highlight_row_index=0,
            row_number=row_number,
            url=url,
        )
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building sheet update card: %s", e)
        return None


def build_calendar_event(tool_result: Any) -> dict | None:
    try:
        payload = _safe_load_payload(tool_result)
        events: list[CalendarEventItem] = []

        if isinstance(payload, dict):
            raw_events = payload.get("events") or payload.get("items") or [payload]
            for ev in raw_events:
                if not isinstance(ev, dict) or ("summary" not in ev and "title" not in ev):
                    continue
                title = ev.get("summary") or ev.get("title") or "Event"
                start = _clean_iso_datetime(str(ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or ev.get("start") or ""))
                end = _clean_iso_datetime(str(ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date") or ev.get("end") or ""))
                attendees = [
                    a.get("email") if isinstance(a, dict) else str(a)
                    for a in ev.get("attendees", [])
                ]
                meet_url = _validate_meet_url(ev.get("hangoutLink") or ev.get("meet_url"))
                html_link = ev.get("htmlLink") or ev.get("html_link")
                status = ev.get("status")
                events.append(
                    CalendarEventItem(
                        title=title,
                        start=start,
                        end=end,
                        attendees=attendees,
                        meet_url=meet_url,
                        html_link=html_link,
                        status=status,
                    )
                )
        elif isinstance(payload, str):
            # 1. Creation / update confirmation
            # Matches:
            # - Successfully created event '...' for ... Link: ... Google Meet: ...
            # - Successfully modified event '...' (ID: ...) for ... Link: ... Conference: ...
            created_m = re.search(
                r"Successfully (?:created|updated|modified) event '([^']+)'(?: \(ID:[^)]+\))?(?: for (.*?)(?: to (.*?)(?:\s*\((.*?)\))?)?)?(?:\.|$)",
                payload,
                re.IGNORECASE,
            )
            if created_m:
                title = created_m.group(1).strip()
                start = _clean_iso_datetime(created_m.group(2) or "")
                end = _clean_iso_datetime(created_m.group(3) or "")
                link_m = re.search(r"Link:\s*([^\s\r\n]+)", payload)
                att_m = re.search(r"Attendees:\s*(.*?)(?:\.\s*Link:|\.\s*$|Link:|$)", payload)
                attendees = [a.strip() for a in att_m.group(1).split(",") if a.strip()] if att_m else []

                meet_m = MEET_URL_PATTERN.search(payload)
                meet_url = _validate_meet_url(meet_m.group(1)) if meet_m else None

                events.append(
                    CalendarEventItem(
                        title=title,
                        start=start,
                        end=end,
                        attendees=attendees,
                        meet_url=meet_url,
                        html_link=link_m.group(1).strip() if link_m and link_m.group(1).strip() != "No Link" else None,
                        status="confirmed",
                    )
                )
            elif re.search(r"Event (?:created|updated|modified) successfully", payload, re.IGNORECASE):
                title_m = re.search(r"(?:Title|Summary|Event):\s*([^\r\n]+)", payload, re.IGNORECASE)
                starts_m = re.search(r"(?:Starts?|Start Time):\s*([^\r\n]+)", payload, re.IGNORECASE)
                ends_m = re.search(r"(?:Ends?|End Time):\s*([^\r\n]+)", payload, re.IGNORECASE)
                link_m = re.search(r"Link:\s*([^\s\r\n]+)", payload)
                meet_m = MEET_URL_PATTERN.search(payload)
                meet_url = _validate_meet_url(meet_m.group(1)) if meet_m else None

                att_list = []
                att_section = re.search(r"Attendees:\s*([\s\S]*?)(?=\n-\s*[A-Za-z]+:|\n[A-Za-z]+:|\Z)", payload)
                if att_section:
                    att_str = att_section.group(1).strip()
                    for att_item in att_str.split(","):
                        cleaned_att = re.sub(r"^[-*\s]+", "", att_item).strip()
                        if cleaned_att and not cleaned_att.startswith("Link"):
                            att_list.append(cleaned_att)

                events.append(
                    CalendarEventItem(
                        title=title_m.group(1).strip() if title_m else "Scheduled Event",
                        start=_clean_iso_datetime(starts_m.group(1).strip()) if starts_m else "",
                        end=_clean_iso_datetime(ends_m.group(1).strip()) if ends_m else "",
                        attendees=att_list,
                        meet_url=meet_url,
                        html_link=link_m.group(1).strip() if link_m and link_m.group(1).strip() != "No Link" else None,
                        status="confirmed",
                    )
                )

            # 2. Single event format (e.g. from get_events with detailed=True or single event):
            # Event Details:
            # - Title: Team Sync
            # - Starts: 2026-03-29 10:00:00
            # - Ends: ...
            # - Link: ...
            if not events and (
                "Event Details:" in payload
                or (re.search(r"(?:^|\n)(?:Title|Summary|Event):\s*", payload, re.IGNORECASE) and "Successfully retrieved" not in payload and not payload.strip().startswith("-"))
            ):
                title_m = re.search(r"(?:Title|Summary|Event):\s*([^\r\n]+)", payload, re.IGNORECASE)
                starts_m = re.search(r"(?:Starts?|Start Time):\s*([^\r\n]+)", payload, re.IGNORECASE)
                ends_m = re.search(r"(?:Ends?|End Time):\s*([^\r\n]+)", payload, re.IGNORECASE)
                link_m = re.search(r"Link:\s*([^\s\r\n]+)", payload)
                meet_m = MEET_URL_PATTERN.search(payload)
                meet_url = _validate_meet_url(meet_m.group(1)) if meet_m else None

                # Extract attendees if present
                att_list = []
                att_section = re.search(r"Attendees:\s*([\s\S]*?)(?=\n-\s*[A-Za-z]+:|\n[A-Za-z]+:|\Z)", payload)
                if att_section:
                    att_str = att_section.group(1).strip()
                    if "\n" in att_str:
                        for att_line in att_str.splitlines():
                            att_line = re.sub(r"^[-*\s]+", "", att_line).strip()
                            if att_line and not att_line.startswith("Event ID") and not att_line.startswith("Link"):
                                att_list.append(att_line)
                    else:
                        for a in att_str.split(","):
                            a = a.strip()
                            if a and a != "None":
                                att_list.append(a)

                if title_m:
                    events.append(
                        CalendarEventItem(
                            title=title_m.group(1).strip(),
                            start=_clean_iso_datetime(starts_m.group(1).strip()) if starts_m else "",
                            end=_clean_iso_datetime(ends_m.group(1).strip()) if ends_m else "",
                            attendees=att_list,
                            meet_url=meet_url,
                            html_link=link_m.group(1).strip() if link_m and link_m.group(1).strip() != "No Link" else None,
                            status=None,  # Not confirmed on read
                        )
                    )

            # 3. Multiple events format (from get_events):
            # - "Title" (Starts: ..., Ends: ...) Meeting: ... ID: ... | Link: ...
            if not events:
                multi_matches = re.finditer(
                    r'-\s*"([^"]+)"\s*\((?:Starts?:\s*([^,]+),\s*Ends?:\s*([^)]+))\)([\s\S]*?)(?=(?:\n-\s*"|\Z))',
                    payload,
                )
                for m in multi_matches:
                    title = m.group(1).strip()
                    start = _clean_iso_datetime(m.group(2).strip())
                    end = _clean_iso_datetime(m.group(3).strip())
                    segment = m.group(4)

                    # Scope Link: and Meeting: to THIS event's segment only
                    link_m = re.search(r"Link:\s*([^\s\r\n]+)", segment)
                    link = link_m.group(1).strip() if link_m and link_m.group(1).strip() != "No Link" else None

                    meet_m = MEET_URL_PATTERN.search(segment)
                    meet_url = _validate_meet_url(meet_m.group(1)) if meet_m else None

                    events.append(
                        CalendarEventItem(
                            title=title,
                            start=start,
                            end=end,
                            meet_url=meet_url,
                            html_link=link,
                            status=None,
                        )
                    )

        if not events:
            return None

        data = CalendarEventData(events=events)
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building calendar event card: %s", e)
        return None


def _clean_search_domain(domain: str) -> str:
    """Normalize domain by lowercasing and removing 'www.' prefix."""
    if not domain:
        return "source"
    d = domain.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    return d


def _is_social_or_comment_domain(domain: str) -> bool:
    """Check if domain is a social media or comment platform."""
    clean_d = _clean_search_domain(domain)
    for blocked in SOCIAL_OR_COMMENT_DOMAINS:
        if clean_d == blocked or clean_d.endswith("." + blocked):
            return True
    return False


def _clean_search_sentence(raw: str) -> str:
    """Strip markdown headers, bold, bullets, backticks into a clean sentence."""
    if not raw:
        return ""
    # Strip markdown headings like ### Title
    cleaned = re.sub(r"#{1,6}\s*", "", raw)
    # Strip bullet indicators
    cleaned = re.sub(r"^[-*•▪▫\d.)\s]+", "", cleaned)
    # Strip bold / italics / code markers
    cleaned = re.sub(r"[*_`]", "", cleaned)
    cleaned = cleaned.strip()
    # Ensure one sentence: split at first period followed by space
    first_sent = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    return first_sent.strip()


def build_search_summary(tool_result: Any) -> dict | None:
    try:
        payload = _safe_load_payload(tool_result)
        title = "Search Results"
        points: list[SearchPoint] = []
        sources: list[SearchSource] = []
        seen_domains = set()

        raw_results = []
        if isinstance(payload, dict):
            raw_results = payload.get("results") or payload.get("items") or []
            if payload.get("query"):
                title = f"Search: {payload['query']}"
        elif isinstance(payload, list):
            raw_results = payload

        if raw_results:
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                url = item.get("url") or ""
                raw_domain = urlparse(url).netloc if url else ""
                clean_dom = _clean_search_domain(raw_domain)

                # Skip social-media / comment domains
                if _is_social_or_comment_domain(clean_dom):
                    continue

                if clean_dom and clean_dom not in seen_domains:
                    seen_domains.add(clean_dom)
                    sources.append(SearchSource(domain=clean_dom, url=url))

                content = item.get("content") or item.get("snippet") or item.get("title") or ""
                if content:
                    # Skip comment threads
                    if re.search(r"^(?:Replying to|View more comments|\d+ comments|\d+ points|Posted by)\b", content.strip(), re.IGNORECASE):
                        continue
                    cleaned_point = _clean_search_sentence(content)
                    if cleaned_point and len(cleaned_point) > 15:
                        points.append(SearchPoint(tag=clean_dom, text=cleaned_point))
        elif isinstance(payload, str):
            # Parse text with URLs
            url_matches = re.findall(r"https?://[^\s)\]]+", payload)
            for url in url_matches:
                raw_domain = urlparse(url).netloc
                clean_dom = _clean_search_domain(raw_domain)
                if _is_social_or_comment_domain(clean_dom):
                    continue
                if clean_dom and clean_dom not in seen_domains:
                    seen_domains.add(clean_dom)
                    sources.append(SearchSource(domain=clean_dom, url=url))

            for line in payload.splitlines():
                line = line.strip()
                if line.startswith("-") or line.startswith("*") or (len(line) > 3 and line[:2].isdigit() and line[2] in ".)"):
                    cleaned_point = _clean_search_sentence(line)
                    if cleaned_point and len(cleaned_point) > 15:
                        default_tag = sources[0].domain if sources else "Summary"
                        points.append(SearchPoint(tag=default_tag, text=cleaned_point))

        if not sources and not points:
            return None

        data = SearchSummaryData(
            title=title,
            source_count=len(sources),
            points=points[:5],
            sources=sources[:5],
        )
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building search summary card: %s", e)
        return None
