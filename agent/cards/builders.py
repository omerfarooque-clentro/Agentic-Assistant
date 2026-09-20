import ast
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

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
        name, email = match.group(1).strip().strip('"'), match.group(2).strip()
        return name or email, email
    return raw.strip(), raw.strip()


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
                items.append(
                    EmailItem(
                        id=msg_id,
                        sender_name=name,
                        sender_email=email,
                        subject=item.get("subject") or "(No Subject)",
                        snippet=item.get("snippet") or item.get("body", "")[:150],
                        received_at=item.get("date") or item.get("received_at") or "",
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
                items.append(
                    EmailItem(
                        id=msg_id,
                        sender_name=name,
                        sender_email=email,
                        subject=item.get("subject") or "(No Subject)",
                        snippet=item.get("snippet") or item.get("body", "")[:150],
                        received_at=item.get("date") or item.get("received_at") or "",
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
                received_at = date_m.group(1).strip() if date_m and "(unknown date)" not in date_m.group(1) else ""
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

        total = len(items)
        unread = sum(1 for it in items if it.unread)
        data = EmailListData(
            query=query or "Recent emails",
            total=total,
            unread=unread,
            items=items,
        )
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building email list card: %s", e)
        return None


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
    match = re.match(r"^(.*?)\s*<([^>]+)>$", s)
    if match:
        name = match.group(1).strip().strip('"')
        email = match.group(2).strip()
        return name or email
    return s or "User"


def _clean_slack_text(raw: str) -> str:
    """Clean message body, stripping trailing Context after/before metadata blocks."""
    if not raw:
        return ""
    cleaned = re.split(r"\bContext (?:after|before):\s*", raw, flags=re.IGNORECASE)[0]
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
                        parsed_val = ast.literal_eval(val_str)
                        if isinstance(parsed_val, list):
                            parsed_rows.append(parsed_val)
                    except Exception:
                        pass
            if parsed_rows:
                columns = [str(c) for c in parsed_rows[0]]
                rows = parsed_rows[1:] if len(parsed_rows) > 1 else []

        if not columns and not rows:
            return None

        data = SheetViewData(title=title, columns=columns, rows=rows, url=url)
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building sheet view card: %s", e)
        return None


def build_sheet_update(tool_result: Any) -> dict | None:
    try:
        payload = _safe_load_payload(tool_result)
        title = "Sheet Updated"
        columns: list[str] = []
        rows: list[list[Any]] = []
        row_number: int | None = None
        url: str | None = None

        if isinstance(payload, dict):
            title = payload.get("title") or payload.get("range") or title
            url = payload.get("url")
            columns = payload.get("columns", [])
            rows = payload.get("rows") or payload.get("values") or []
            row_number = payload.get("row_number")
        elif isinstance(payload, str):
            range_m = re.search(r"range '([^']+)'", payload)
            if range_m:
                range_name = range_m.group(1)
                title = f"Updated {range_name}"
                # Try finding row number from range like Sheet1!A5:D5
                clean_range = range_name.split("!")[-1] if "!" in range_name else range_name
                num_m = re.search(r"[A-Za-z]+(\d+)", clean_range)
                if num_m:
                    row_number = int(num_m.group(1))

            sheet_id_m = re.search(r"spreadsheet ([A-Za-z0-9_-]+)", payload)
            if sheet_id_m:
                url = f"https://docs.google.com/spreadsheets/d/{sheet_id_m.group(1)}/edit"

            # Check for appended row message
            append_m = re.search(r"appended (\d+) row\(s\) to table '([^']+)'", payload)
            if append_m:
                title = f"Appended to {append_m.group(2)}"

            # If values were rendered in output
            for line in payload.splitlines():
                if line.strip().startswith("Row") and ":" in line:
                    val_str = line.split(":", 1)[1].strip()
                    try:
                        parsed_val = ast.literal_eval(val_str)
                        if isinstance(parsed_val, list):
                            rows.append(parsed_val)
                    except Exception:
                        pass

        if not rows and not columns and not url and not row_number:
            # At least need title or success confirmation
            if isinstance(payload, str) and "Successfully" in payload:
                title = "Sheet Updated Successfully"
            else:
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
                start = str(ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date") or ev.get("start") or "")
                end = str(ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date") or ev.get("end") or "")
                attendees = [
                    a.get("email") if isinstance(a, dict) else str(a)
                    for a in ev.get("attendees", [])
                ]
                meet_url = ev.get("hangoutLink") or ev.get("meet_url")
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
            # Check for creation/update confirmation
            # e.g.: Successfully created event 'Strategic Partnership Kickoff' for 2026-03-23T15:00:00 to 2026-03-23T16:00:00 (UTC). Attendees: ... Link: ...
            created_m = re.search(
                r"Successfully (?:created|updated) event '([^']+)'(?: for (.*?)(?: to (.*?)(?:\s*\((.*?)\))?)?)?(?:\.|$)",
                payload,
            )
            if created_m:
                title = created_m.group(1).strip()
                start = (created_m.group(2) or "").strip()
                end = (created_m.group(3) or "").strip()
                link_m = re.search(r"Link:\s*([^\s\r\n]+)", payload)
                att_m = re.search(r"Attendees:\s*(.*?)(?:\.\s*Link:|\.\s*$|Link:|$)", payload)
                attendees = [a.strip() for a in att_m.group(1).split(",") if a.strip()] if att_m else []
                events.append(
                    CalendarEventItem(
                        title=title,
                        start=start,
                        end=end,
                        attendees=attendees,
                        html_link=link_m.group(1).strip() if link_m and link_m.group(1).strip() != "No Link" else None,
                        status="confirmed",
                    )
                )

            # Single event format:
            # - Title: Team Sync\n- Starts: 2026-03-29 10:00:00\n- Ends: ...\n- Link: ...
            if not events and ("- Title:" in payload or "Title:" in payload):
                title_m = re.search(r"Title:\s*([^\r\n]+)", payload)
                starts_m = re.search(r"Starts?:\s*([^\r\n]+)", payload)
                ends_m = re.search(r"Ends?:\s*([^\r\n]+)", payload)
                link_m = re.search(r"Link:\s*([^\r\n]+)", payload)
                if title_m:
                    events.append(
                        CalendarEventItem(
                            title=title_m.group(1).strip(),
                            start=starts_m.group(1).strip() if starts_m else "",
                            end=ends_m.group(1).strip() if ends_m else "",
                            html_link=link_m.group(1).strip() if link_m and link_m.group(1).strip() != "No Link" else None,
                        )
                    )
            # Multiple events format: - "Title" (Starts: ..., Ends: ...)\n Link: ...
            if not events:
                multi_matches = re.finditer(
                    r'-\s*"([^"]+)"\s*\((?:Starts?:\s*([^,]+),\s*Ends?:\s*([^)]+))\)(?:[\s\S]*?Link:\s*([^\s\r\n]+))?',
                    payload,
                )
                for m in multi_matches:
                    link = m.group(4) if m.group(4) and m.group(4) != "No Link" else None
                    events.append(
                        CalendarEventItem(
                            title=m.group(1).strip(),
                            start=m.group(2).strip(),
                            end=m.group(3).strip(),
                            html_link=link,
                        )
                    )

        if not events:
            return None

        data = CalendarEventData(events=events)
        return data.model_dump()
    except Exception as e:
        logger.debug("Failed building calendar event card: %s", e)
        return None


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
                domain = urlparse(url).netloc if url else "source"
                if domain and domain not in seen_domains:
                    seen_domains.add(domain)
                    sources.append(SearchSource(domain=domain, url=url))

                content = item.get("content") or item.get("snippet") or item.get("title") or ""
                if content:
                    first_sent = content.strip().split(". ")[0]
                    points.append(SearchPoint(tag=item.get("title", "Result")[:30], text=first_sent))
        elif isinstance(payload, str):
            # Parse text with URLs
            url_matches = re.findall(r"https?://[^\s)\]]+", payload)
            for url in url_matches:
                domain = urlparse(url).netloc
                if domain and domain not in seen_domains:
                    seen_domains.add(domain)
                    sources.append(SearchSource(domain=domain, url=url))

            for line in payload.splitlines():
                line = line.strip()
                if (line.startswith("-") or line.startswith("*") or (len(line) > 3 and line[:2].isdigit() and line[2] in ".)")):
                    clean = re.sub(r"^[-*\d.)\s]+", "", line).strip()
                    if clean and len(clean) > 10:
                        tag = clean.split(":")[0][:25] if ":" in clean else "Key Point"
                        points.append(SearchPoint(tag=tag, text=clean))

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
