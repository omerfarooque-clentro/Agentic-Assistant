"""Pure-Python B2B Lead Generation & Google Sheets Export Tool for the Research Agent.

Architecture:
1. Executes the 7-Gate Deterministic Lead Generation Pipeline in pure Python.
2. Enforces 1 Unique Company per Lead (no duplicate CEOs, no fake phone/LinkedIn guessing).
3. Pure Python writes all leads directly to Google Sheets (via Google Sheets REST API v4
   using the user's connected OAuth token) AND exports locally to CSV/JSONL, so the LLM
   agent NEVER wastes tokens or hallucinates while writing rows to a spreadsheet.
4. Returns raw numerical counts, verification axis metrics, regional quotas, and sample rows
   so the LLM agent performs genuine mathematical reasoning (yield rates, regional fill
   percentages, bounce-risk analysis, and conversion projections).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from langchain_core.tools import tool

from lead_generation.exporting import CsvExporter, JsonLinesExporter, lead_to_flat_dict
from lead_generation.filtering import parse_company_size_bounds
from lead_generation.models import CampaignCriteria, LeadRecord, MAX_CAMPAIGN_QUOTA
from lead_generation.pipeline import LeadGenerationPipeline

logger = logging.getLogger(__name__)

SHEET_HEADERS = [
    "Lead Grade",
    "Company Name",
    "Domain",
    "Location",
    "Hiring Signal (Job Title)",
    "Job Posting URL",
    "Executive Name",
    "Executive Role",
    "LinkedIn URL",
    "Best Email",
    "Phone Number",
    "Phone Source",
    "Email Attribution",
    "Mail Provider (MX)",
    "Verification Status",
]


def _parse_locations_arg(locations: str | list[str] | None) -> tuple[str, ...]:
    """Parse a comma/slash/and-separated string or list of target countries/regions."""
    if not locations:
        return ("US", "UK", "Remote")
    if isinstance(locations, (list, tuple)):
        raw_items = [str(x).strip() for x in locations if str(x).strip()]
    else:
        cleaned = re.sub(r"\b(?:and|or)\b", ",", str(locations), flags=re.IGNORECASE)
        raw_items = [p.strip() for p in re.split(r"[,;/]+", cleaned) if p.strip()]
    return tuple(raw_items) if raw_items else ("US", "UK", "Remote")


def _get_live_google_sheets_token(user: Any) -> str | None:
    """Retrieve and if needed refresh the user's Google Sheets OAuth access token in pure Python."""
    if user and getattr(user, "is_authenticated", False):
        try:
            from agent.models import MCPIntegration
        except Exception:
            MCPIntegration = None
        integration = (
            MCPIntegration.objects.filter(
                user=user,
                service__in=["sheets", "google"],
                enabled=True,
            )
            .order_by("-updated_at")
            .first()
            if MCPIntegration is not None
            else None
        )
        if integration and integration.access_token:
            # Refresh token if expired or expiring within 2 minutes
            if (
                integration.refresh_token
                and integration.expires_at
                and integration.expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2)
            ):
                try:
                    resp = requests.post(
                        "https://oauth2.googleapis.com/token",
                        data={
                            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                            "refresh_token": integration.refresh_token,
                            "grant_type": "refresh_token",
                        },
                        timeout=15,
                    )
                    if resp.ok:
                        token_data = resp.json()
                        integration.access_token = token_data["access_token"]
                        integration.expires_at = datetime.now(timezone.utc) + timedelta(
                            seconds=token_data.get("expires_in", 3600)
                        )
                        integration.save(update_fields=["access_token", "expires_at", "updated_at"])
                except Exception as exc:
                    logger.warning("Failed refreshing Google Sheets token in pure Python writer: %s", exc)
            return integration.access_token

    return os.getenv("GOOGLE_ACCESS_TOKEN") or os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN")


def _lead_to_sheet_row(lead: LeadRecord | dict[str, Any]) -> list[str]:
    """Convert a LeadRecord or flat dict into a 15-column Google Sheets row."""
    d = lead_to_flat_dict(lead) if isinstance(lead, LeadRecord) else lead
    return [
        str(d.get("lead_grade") or ""),
        str(d.get("company_name") or ""),
        str(d.get("canonical_domain") or ""),
        str(d.get("location_bucket") or ""),
        str(d.get("job_title_signal") or ""),
        str(d.get("job_url_signal") or ""),
        str(d.get("exec_full_name") or ""),
        str(d.get("exec_title") or ""),
        str(d.get("linkedin_url") or ""),
        str(d.get("best_email") or ""),
        str(d.get("phone_number") or ""),
        str(d.get("phone_type") or "NONE"),
        str(d.get("attribution_status") or ""),
        str(d.get("inferred_provider") or ""),
        str(d.get("smtp_status") or ""),
    ]


def write_leads_to_google_sheet_pure_python(
    user: Any,
    leads: list[LeadRecord],
    role: str,
    locations: tuple[str, ...],
    spreadsheet_id: str | None = None,
) -> dict[str, Any]:
    """
    Pure-Python bulk writer to Google Sheets REST API v4.
    The LLM agent never writes rows manually; Python creates/appends all rows in one HTTP call.
    """
    rows = [_lead_to_sheet_row(lead) for lead in leads[:MAX_CAMPAIGN_QUOTA]]
    token = _get_live_google_sheets_token(user)
    if not token:
        return {
            "synced_to_google_sheets": False,
            "reason": "GOOGLE_SHEETS_NOT_CONNECTED",
            "rows_prepared": len(rows),
            "spreadsheet_url": None,
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    now_label = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sheet_title = f"B2B Leads - {role.title()} ({', '.join(locations[:4])}) - {now_label}"

    try:
        target_sheet_id = (spreadsheet_id or "").strip() or None
        sheet_url = None

        if not target_sheet_id:
            create_payload = {
                "properties": {"title": sheet_title},
                "sheets": [
                    {
                        "properties": {
                            "title": "Verified Leads",
                            "gridProperties": {
                                "rowCount": max(len(rows) + 10, 50),
                                "columnCount": len(SHEET_HEADERS),
                                "frozenRowCount": 1,
                            },
                        }
                    }
                ],
            }
            create_resp = requests.post(
                "https://sheets.googleapis.com/v4/spreadsheets",
                headers=headers,
                json=create_payload,
                timeout=20,
            )
            create_resp.raise_for_status()
            created_data = create_resp.json()
            target_sheet_id = created_data.get("spreadsheetId")
            sheet_url = created_data.get("spreadsheetUrl") or (
                f"https://docs.google.com/spreadsheets/d/{target_sheet_id}/edit" if target_sheet_id else None
            )
            range_name = "Verified Leads!A1"
            values_to_write = [SHEET_HEADERS, *rows]
        else:
            sheet_url = f"https://docs.google.com/spreadsheets/d/{target_sheet_id}/edit"
            range_name = "A1"
            values_to_write = [SHEET_HEADERS, *rows]

        append_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{target_sheet_id}/values/"
            f"{requests.utils.quote(range_name)}:append"
        )
        append_resp = requests.post(
            append_url,
            headers=headers,
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": values_to_write},
            timeout=25,
        )
        append_resp.raise_for_status()

        return {
            "synced_to_google_sheets": True,
            "spreadsheet_id": target_sheet_id,
            "spreadsheet_title": sheet_title,
            "spreadsheet_url": sheet_url,
            "rows_inserted": len(rows),
        }
    except Exception as exc:
        logger.warning("Pure-Python Google Sheets insertion failed: %s", exc)
        return {
            "synced_to_google_sheets": False,
            "reason": str(exc),
            "rows_prepared": len(rows),
            "spreadsheet_url": None,
        }


def execute_lead_generation_and_export(
    user: Any,
    role: str = "Python Developer",
    locations: str | list[str] = "US, UK, Remote",
    company_size: str = "10-50",
    quota: int = 100,
    spreadsheet_id: str | None = None,
    enable_dom_scrape: bool = True,
    enable_live_smtp: bool = False,
    progress_callback: Any | None = None,
    should_cancel: Any | None = None,
    emit_langchain_events: bool = True,
) -> dict[str, Any]:
    """
    Runs the deterministic 7-Gate Lead Generation Pipeline, exports to local CSV/JSONL and
    Google Sheets in pure Python, and returns raw telemetry counts for the LLM agent to analyze.
    """
    start_ts = time.perf_counter()
    parsed_locations = _parse_locations_arg(locations)
    bounds = parse_company_size_bounds(company_size)
    min_size = bounds[0] if bounds else 10
    max_size = bounds[1] if bounds else 50
    capped_quota = max(1, min(int(quota or 100), MAX_CAMPAIGN_QUOTA))

    criteria = CampaignCriteria(
        role=role.strip() or "Software Engineer",
        locations=parsed_locations,
        quota=capped_quota,
        min_company_size=min_size,
        max_company_size=max_size,
    )

    def _emit_step(step_info: dict[str, Any]) -> None:
        if callable(progress_callback):
            try:
                progress_callback(step_info)
            except Exception:
                pass
        if emit_langchain_events:
            try:
                from langchain_core.callbacks.manager import dispatch_custom_event

                dispatch_custom_event("lead_progress", step_info)
            except Exception:
                pass

    pipeline = LeadGenerationPipeline()
    campaign_result = pipeline.run_campaign(
        criteria,
        enable_dom_scrape=enable_dom_scrape,
        enable_web_mentions=True,
        enable_live_smtp=enable_live_smtp,
        require_linkedin=True,
        persist_outbox=True,
        progress_callback=_emit_step,
        should_cancel=should_cancel,
    )

    leads: list[LeadRecord] = campaign_result["leads"]
    boards = campaign_result.get("boards", [])
    matched_jobs = campaign_result.get("jobs", [])
    dropped_companies = campaign_result.get("dropped_companies", [])
    regional_quotas = campaign_result.get("regional_quotas", {})

    _emit_step(
        {
            "step": "syncing_sheets",
            "message": f"Searching ({len(leads)}/{capped_quota}) → Syncing {len(leads)} verified leads to Google Sheets & CSV...",
            "completed": len(leads),
            "quota": capped_quota,
        }
    )

    # 1. Pure-Python Local CSV & JSONL Export
    output_dir = os.path.join(os.getcwd(), "outputs")
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "leads_deliverable.csv")
    jsonl_path = os.path.join(output_dir, "leads_deliverable.jsonl")

    CsvExporter(csv_path).export(leads)
    JsonLinesExporter(jsonl_path).export([lead_to_flat_dict(l) for l in leads])

    # 2. Pure-Python Google Sheets Bulk Insertion
    sheet_export = write_leads_to_google_sheet_pure_python(
        user=user,
        leads=leads,
        role=criteria.role,
        locations=parsed_locations,
        spreadsheet_id=spreadsheet_id,
    )

    # 3. Aggregate Raw Counts for LLM Mathematical & Confidence Reasoning
    grade_counts = {"GRADE_A": 0, "GRADE_B": 0, "GRADE_C": 0, "REVIEW_QUEUE": 0}
    identity_confidence_counts: dict[str, int] = {}
    routing_status_counts: dict[str, int] = {}
    provider_counts: dict[str, int] = {}
    attribution_counts: dict[str, int] = {}
    smtp_counts: dict[str, int] = {}
    location_counts: dict[str, int] = {}
    with_linkedin = 0
    with_email = 0
    with_phone = 0

    for lead in leads:
        g_key = lead.lead_grade.value
        grade_counts[g_key] = grade_counts.get(g_key, 0) + 1

        id_conf = lead.identity_confidence.value
        identity_confidence_counts[id_conf] = identity_confidence_counts.get(id_conf, 0) + 1

        r_stat = lead.company.routing_status.value
        routing_status_counts[r_stat] = routing_status_counts.get(r_stat, 0) + 1

        prov = lead.company.inferred_provider or "UNCLASSIFIED"
        provider_counts[prov] = provider_counts.get(prov, 0) + 1

        attr = lead.attribution_status.value
        attribution_counts[attr] = attribution_counts.get(attr, 0) + 1

        smtp = lead.smtp_status.value
        smtp_counts[smtp] = smtp_counts.get(smtp, 0) + 1

        loc_b = lead.location_bucket or "Unspecified"
        location_counts[loc_b] = location_counts.get(loc_b, 0) + 1

        if lead.linkedin_url and "linkedin.com/in/" in lead.linkedin_url:
            with_linkedin += 1
        if lead.best_email:
            with_email += 1
        if lead.phone_number:
            with_phone += 1

    dropped_by_reason: dict[str, int] = {}
    for d in dropped_companies:
        reason = d.get("reason") or d.get("routing_status") or "OTHER"
        dropped_by_reason[reason] = dropped_by_reason.get(reason, 0) + 1

    elapsed_sec = round(time.perf_counter() - start_ts, 2)
    # Cap UI card preview at 5 rows so the entire ToolMessage stays ~480 tokens (<2,000 chars),
    # well below MAX_TOOL_MESSAGE_CHARS (3,000 chars), while all 100 rows are written to Sheets by Python.
    preview_rows = [_lead_to_sheet_row(l) for l in leads[:5]]

    return {
        "title": sheet_export.get("spreadsheet_title") or f"B2B Leads ({criteria.role})",
        "url": sheet_export.get("spreadsheet_url"),
        "download_csv_url": "/api/leads/download/csv/",
        "download_jsonl_url": "/api/leads/download/jsonl/",
        "total_rows": len(leads),
        "synced_to_google_sheets": bool(sheet_export.get("synced_to_google_sheets")),
        "campaign_parameters": {
            "target_role": criteria.role,
            "target_locations": list(parsed_locations),
            "company_size_filter": f"{min_size}-{max_size} employees",
            "requested_quota": capped_quota,
            "regional_quota_allocation": regional_quotas,
        },
        "raw_funnel_counts": {
            "ats_boards_scanned": len(boards),
            "filtered_vacancies_matched": len(matched_jobs),
            "unique_companies_evaluated": len(leads) + len(dropped_companies),
            "unique_verified_leads_exported": len(leads),
            "companies_dropped_total": len(dropped_companies),
            "companies_dropped_by_reason": dropped_by_reason,
            "leads_with_verified_linkedin": with_linkedin,
            "leads_with_corporate_email": with_email,
            "leads_with_verified_phone": with_phone,
        },
        "grade_distribution_counts": grade_counts,
        "identity_confidence_counts": identity_confidence_counts,
        "routing_status_counts": routing_status_counts,
        "mail_provider_counts": provider_counts,
        "email_attribution_counts": attribution_counts,
        "verification_status_counts": smtp_counts,
        "location_distribution_counts": location_counts,
        "pure_python_export": {
            **sheet_export,
            "csv_file_path": csv_path,
            "jsonl_file_path": jsonl_path,
            "download_csv_url": "/api/leads/download/csv/",
            "download_jsonl_url": "/api/leads/download/jsonl/",
            "execution_seconds": elapsed_sec,
        },
        "agent_math_instructions": (
            "Pure Python has already written all verified leads directly to Google Sheets (if connected) and downloadable CSV (`/api/leads/download/csv/`). "
            "Do NOT call any Google Sheets write tools. "
            "Provide: (1) 3-Axis Epistemic Confidence & Deliverable Grade Summary (GRADE_A/B/C/REVIEW_QUEUE, CURRENT_CONFIRMED identity, MX_RESOLVED routing), "
            "(2) Funnel Yield & Contact Coverage % (LinkedIn, Email, E.164 Phone), and "
            "(3) Cold Outreach Conversion Projection (35% open rate, 8% positive reply rate)."
        ),
        "columns": SHEET_HEADERS,
        "rows": preview_rows,
    }


def create_lead_generation_tool(user: Any = None):
    """Create a LangChain tool bound to the user for deterministic B2B lead generation + pure-Python Sheets export."""

    @tool("tavily_lead_search")
    def tavily_lead_search(
        role: str = "Python Developer",
        locations: str = "US, UK, Australia, UAE, Remote",
        company_size: str = "10-50",
        quota: int = 100,
        spreadsheet_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate verified B2B startup/SMB executive leads (CEO/CTO/Founder email, LinkedIn URL, and published phone number)
        for companies actively hiring for a given developer role across global locations (US, UK, Australia, Dubai/UAE, Canada,
        Europe/Germany/Netherlands, Singapore, Remote/Worldwide) and automatically insert the rows into Google Sheets and CSV via pure Python.

        Always use this tool when the user asks for lead generation, finding companies/startups hiring developers,
        getting CEO/founder emails/LinkedIn/phone numbers, or adding generated leads to a spreadsheet.

        Args:
            role: Target developer or engineering role being hired (e.g. 'Python Developer', 'Full Stack Engineer', 'AI Engineer').
            locations: Comma-separated target countries or regions (e.g. 'US, UK, Australia, Dubai, Remote, Canada, Europe').
            company_size: Employee headcount range filter, defaulting to '10-50' for agile startups/SMBs.
            quota: Target number of unique company leads to generate (capped at 100).
            spreadsheet_id: Optional existing Google Spreadsheet ID to append to; if omitted, Python creates a new Google Sheet automatically.
        """
        return execute_lead_generation_and_export(
            user=user,
            role=role,
            locations=locations,
            company_size=company_size,
            quota=quota,
            spreadsheet_id=spreadsheet_id,
        )

    return tavily_lead_search
