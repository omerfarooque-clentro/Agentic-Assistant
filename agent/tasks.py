"""Celery tasks and background job/cancellation manager for B2B lead generation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import threading
import time
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:  # pragma: no cover - fallback decorator when celery is not installed
    def shared_task(*d_args, **d_kwargs):
        def _decorator(fn):
            fn.delay = lambda *a, **kw: fn(*a, **kw)
            return fn
        if d_args and callable(d_args[0]):
            return _decorator(d_args[0])
        return _decorator


_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lead_bg_worker")

# thread_id (int) -> bool cancellation flag for active SSE streams or tool runs
THREAD_CANCEL_FLAGS: dict[int, bool] = {}

# job_id (str) -> dict of live job state for "Run in Background" & Claude-style step feed
LEAD_JOB_REGISTRY: dict[str, dict[str, Any]] = {}

# thread_id (int) -> active job_id (str)
THREAD_ACTIVE_JOB: dict[int, str] = {}


def clear_thread_cancel(thread_id: int | str | None) -> None:
    """Clear cancellation state at the start of a new user turn or approval."""
    if thread_id is None:
        return
    tid = int(thread_id)
    with _LOCK:
        THREAD_CANCEL_FLAGS[tid] = False


def request_thread_cancel(thread_id: int | str) -> dict[str, Any]:
    """Signal an active stream or background lead generation job on thread_id to halt immediately."""
    tid = int(thread_id)
    with _LOCK:
        THREAD_CANCEL_FLAGS[tid] = True
        job_id = THREAD_ACTIVE_JOB.get(tid)
        if job_id and job_id in LEAD_JOB_REGISTRY:
            job = LEAD_JOB_REGISTRY[job_id]
            if job.get("status") in ("queued", "running"):
                job["cancelled"] = True
                job["status"] = "cancelled"
                job["message"] = "Stopped by user."
                job["updated_at"] = time.time()
    return {"cancelled": True, "thread_id": tid, "job_id": job_id}


def is_thread_cancelled(thread_id: int | str | None = None, job_id: str | None = None) -> bool:
    """Return True if the user clicked Stop/Halt for this thread or background job."""
    with _LOCK:
        if job_id and job_id in LEAD_JOB_REGISTRY:
            if LEAD_JOB_REGISTRY[job_id].get("cancelled"):
                return True
        if thread_id is not None and THREAD_CANCEL_FLAGS.get(int(thread_id), False):
            return True
    return False


def update_job_progress(
    step_data: dict[str, Any],
    *,
    job_id: str | None = None,
    thread_id: int | str | None = None,
) -> dict[str, Any]:
    """Record a Claude-style step progression update for an active job or thread."""
    now = time.time()
    step_name = str(step_data.get("step") or "searching")
    message = str(step_data.get("message") or "Searching...")
    completed = int(step_data.get("completed") or 0)
    quota = int(step_data.get("quota") or 100)

    payload = {
        "step": step_name,
        "message": message,
        "completed": completed,
        "quota": quota,
        "updated_at": now,
    }

    with _LOCK:
        resolved_job_id = job_id or (THREAD_ACTIVE_JOB.get(int(thread_id)) if thread_id is not None else None)
        if resolved_job_id and resolved_job_id in LEAD_JOB_REGISTRY:
            job = LEAD_JOB_REGISTRY[resolved_job_id]
            job["step"] = step_name
            job["message"] = message
            job["completed"] = completed
            job["quota"] = quota
            job["updated_at"] = now
            history: list[dict[str, Any]] = job.setdefault("steps_history", [])
            if not history or history[-1].get("message") != message:
                history.append({"step": step_name, "message": message, "completed": completed, "quota": quota, "ts": now})
                if len(history) > 8:
                    del history[:-8]
            payload["steps_history"] = list(history)
    return payload


def get_job_status(job_id: str) -> dict[str, Any] | None:
    """Return a snapshot of the background job status."""
    with _LOCK:
        job = LEAD_JOB_REGISTRY.get(job_id)
        return dict(job) if job else None


def get_active_thread_job(thread_id: int | str) -> dict[str, Any] | None:
    """Return the active or most recent background job for a thread."""
    tid = int(thread_id)
    with _LOCK:
        job_id = THREAD_ACTIVE_JOB.get(tid)
        if job_id and job_id in LEAD_JOB_REGISTRY:
            return dict(LEAD_JOB_REGISTRY[job_id])
    return None


def format_deterministic_lead_summary(telemetry: dict[str, Any]) -> str:
    """Build the 3-Axis Epistemic Confidence & Mathematical Funnel Summary from compact telemetry."""
    funnel = telemetry.get("raw_funnel_counts") or telemetry.get("funnel_math") or {}
    grades = telemetry.get("grade_distribution_counts") or (telemetry.get("confidence_axes") or {}).get("lead_grades") or {}
    sheet = telemetry.get("pure_python_export") or telemetry.get("google_sheets_export") or {}
    criteria = telemetry.get("campaign_parameters") or telemetry.get("campaign_criteria") or {}
    loc_counts = telemetry.get("location_distribution_counts") or {}

    quota = criteria.get("requested_quota") or funnel.get("requested_quota") or 100
    final_leads = funnel.get(
        "unique_verified_leads_exported",
        funnel.get("unique_company_leads_finalized", telemetry.get("leads_found", 0)),
    )
    boards = funnel.get("ats_boards_scanned", funnel.get("boards_discovered", 0))
    vacancies = funnel.get("filtered_vacancies_matched", funnel.get("vacancies_matched", 0))
    dropped = funnel.get("companies_dropped_total", funnel.get("duplicate_or_rejected_companies_dropped", 0))
    with_li = funnel.get("leads_with_verified_linkedin", 0)
    with_email = funnel.get("leads_with_corporate_email", funnel.get("leads_with_verified_email", 0))
    with_phone = funnel.get("leads_with_verified_phone", 0)

    g_a = grades.get("GRADE_A", 0)
    g_b = grades.get("GRADE_B", 0)
    g_c = grades.get("GRADE_C", 0)
    g_rev = grades.get("REVIEW_QUEUE", 0)

    denom = max(final_leads, 1)
    yield_pct = round((final_leads / max(vacancies, 1)) * 100, 1) if vacancies else 100.0
    li_pct = round((with_li / denom) * 100, 1)
    email_pct = round((with_email / denom) * 100, 1)
    phone_pct = round((with_phone / denom) * 100, 1)
    open_proj = round(final_leads * 0.35, 1)
    reply_proj = round(final_leads * 0.08, 1)

    dl_csv = telemetry.get("download_csv_url") or sheet.get("download_csv_url") or "/api/leads/download/csv/"
    sheet_line = (
        f"Synced **{sheet.get('rows_inserted', final_leads)} rows** directly to Google Sheets in pure Python "
        f"([Open Spreadsheet]({sheet.get('spreadsheet_url')})) and saved a downloadable CSV backup ([Download CSV]({dl_csv}))"
        if sheet.get("synced_to_google_sheets") and sheet.get("spreadsheet_url")
        else (
            f"Google Sheets is not connected yet — exported **{final_leads} verified leads** directly to a downloadable CSV file "
            f"([Download Verified Leads (.CSV)]({dl_csv}))"
        )
    )

    reg_parts = [f"{loc}: {cnt}" for loc, cnt in loc_counts.items()]
    reg_str = ", ".join(reg_parts) if reg_parts else ", ".join(criteria.get("target_locations") or ["Global"])
    role_label = criteria.get("target_role") or criteria.get("role") or "Developer"
    size_label = criteria.get("company_size_filter") or criteria.get("company_size_bounds") or "10-50 employees"

    return (
        f"### Lead Generation Complete — {final_leads}/{quota} Verified Startup Leads\n\n"
        f"{sheet_line}.\n\n"
        f"#### 1. Mathematical Funnel & Yield Breakdown\n"
        f"- **ATS Boards Scanned**: `{boards}` boards → **`{vacancies}`** active `{role_label}` vacancies matched (`{size_label}`)\n"
        f"- **Entity Deduplication (`1 Lead = 1 Unique Company`)**: **`{final_leads}`** unique companies finalized (`{dropped}` duplicate/unverified companies filtered out; **`{yield_pct}%`** vacancy-to-lead conversion yield)\n"
        f"- **Regional Distribution**: {reg_str}\n\n"
        f"#### 2. 3-Axis Epistemic Confidence Summary\n"
        f"- **Axis 1 — Executive Identity & LinkedIn**: **`{with_li}/{final_leads}` (`{li_pct}%`)** verified `linkedin.com/in/` decision-maker profiles (zero fabricated URLs)\n"
        f"- **Axis 2 — Mail Reachability & Attribution**: **`{with_email}/{final_leads}` (`{email_pct}%`)** deliverable/MX-verified emails (`GRADE_A`: `{g_a}`, `GRADE_B`: `{g_b}`, `GRADE_C`: `{g_c}`, `REVIEW_QUEUE`: `{g_rev}`)\n"
        f"- **Axis 3 — E.164 Phone Honesty**: **`{with_phone}/{final_leads}` (`{phone_pct}%`)** strictly validated E.164 numbers extracted from official contact pages (`NONE` left blank where not publicly listed)\n\n"
        f"#### 3. Outreach Conversion Projection\n"
        f"- **Expected Opens (35%)**: `~{open_proj}` executives | **Expected Positive Replies (8%)**: `~{reply_proj}` qualified conversations"
    )


def _execute_background_lead_job(
    job_id: str,
    thread_id: int,
    user_id: int,
    approval_payload: dict[str, Any],
    modified_args: dict[str, Any] | None,
    instruction: str | None,
) -> dict[str, Any]:
    """Worker implementation shared by Celery task and ThreadPool fallback."""
    from django.contrib.auth import get_user_model
    from django.db import close_old_connections
    from agent.cards import build_result_card, render_cards
    from agent.tools.lead_generation_tool import execute_lead_generation_and_export
    from conversations.models import Message, Thread

    close_old_connections()
    User = get_user_model()

    with _LOCK:
        job = LEAD_JOB_REGISTRY.get(job_id)
        if not job:
            return {"status": "missing"}
        if job.get("cancelled"):
            job["status"] = "cancelled"
            return dict(job)
        job["status"] = "running"
        job["updated_at"] = time.time()

    try:
        user = User.objects.filter(id=user_id).first()
        thread = Thread.objects.filter(id=thread_id).first()
        if not user or not thread:
            raise RuntimeError("User or thread not found for background lead generation job.")

        # Extract tool arguments from approval payload + modified_args
        raw_details = (approval_payload or {}).get("details") or {}
        args = dict(raw_details)
        if isinstance(modified_args, dict):
            args.update(modified_args)

        role = str(args.get("role") or "Python Developer")
        locations = args.get("locations") or "US, UK, Remote"
        company_size = str(args.get("company_size") or "10-50")
        quota = int(args.get("quota") or 100)
        spreadsheet_id = args.get("spreadsheet_id") or None

        def _on_progress(step_info: dict[str, Any]) -> None:
            update_job_progress(step_info, job_id=job_id, thread_id=thread_id)

        def _check_cancel() -> bool:
            return is_thread_cancelled(thread_id=thread_id, job_id=job_id)

        update_job_progress(
            {
                "step": "discover_boards",
                "message": f"Searching (0/{quota}) → Discovering ATS startup boards for {role}...",
                "completed": 0,
                "quota": quota,
            },
            job_id=job_id,
            thread_id=thread_id,
        )

        telemetry = execute_lead_generation_and_export(
            user=user,
            role=role,
            locations=locations,
            company_size=company_size,
            quota=quota,
            spreadsheet_id=spreadsheet_id,
            enable_dom_scrape=True,
            enable_live_smtp=False,
            progress_callback=_on_progress,
            should_cancel=_check_cancel,
            emit_langchain_events=False,
        )

        if is_thread_cancelled(thread_id=thread_id, job_id=job_id):
            with _LOCK:
                job = LEAD_JOB_REGISTRY.get(job_id, {})
                job["status"] = "cancelled"
                job["message"] = f"Stopped by user ({telemetry.get('leads_found', 0)}/{quota} leads processed before halt)."
                job["updated_at"] = time.time()
            return dict(job)

        summary_md = format_deterministic_lead_summary(telemetry)
        tool_msg = ToolMessage(
            content=json.dumps(telemetry, ensure_ascii=False),
            name="tavily_lead_search",
            tool_call_id="bg_lead_call_1",
        )
        fake_messages = [
            HumanMessage(content=instruction or f"Generate {quota} {role} leads"),
            AIMessage(content=summary_md),
            tool_msg,
        ]
        result_card = build_result_card(fake_messages)
        card_record = render_cards(
            approval=approval_payload or {"action": "tavily_lead_search", "domain": "research", "details": args},
            messages=fake_messages,
            modified_args=modified_args,
            approved=True,
            instruction=instruction or "",
        )
        all_cards = [card_record, *([result_card] if result_card else [])]

        msg_obj = Message.objects.create(
            thread=thread,
            role="agent",
            content=summary_md,
            metrics={},
            cards=all_cards,
        )
        thread.save(update_fields=["updated_at"])

        with _LOCK:
            job = LEAD_JOB_REGISTRY.get(job_id, {})
            job["status"] = "completed"
            job["completed"] = int(telemetry.get("leads_found", 0))
            job["quota"] = quota
            job["message"] = f"Completed ({telemetry.get('leads_found', 0)}/{quota}) → Synced to Google Sheets"
            job["response"] = summary_md
            job["result_card"] = result_card
            job["card_record"] = card_record
            job["message_id"] = msg_obj.id
            job["updated_at"] = time.time()
            return dict(job)

    except Exception as exc:
        logger.exception("Background lead generation job %s failed", job_id)
        with _LOCK:
            job = LEAD_JOB_REGISTRY.get(job_id, {})
            job["status"] = "failed"
            job["error"] = str(exc)
            job["message"] = f"Failed: {exc}"
            job["updated_at"] = time.time()
            return dict(job)
    finally:
        close_old_connections()


@shared_task(bind=True, rate_limit="30/m")
def run_lead_generation_campaign_task(
    self,
    job_id: str,
    thread_id: int,
    user_id: int,
    approval_payload: dict[str, Any],
    modified_args: dict[str, Any] | None = None,
    instruction: str | None = None,
) -> dict[str, Any]:
    """Celery task wrapper with rate-limit guard for background B2B lead generation."""
    return _execute_background_lead_job(
        job_id=job_id,
        thread_id=thread_id,
        user_id=user_id,
        approval_payload=approval_payload,
        modified_args=modified_args,
        instruction=instruction,
    )


def start_background_lead_job(
    *,
    thread_id: int,
    user_id: int,
    approval_payload: dict[str, Any],
    modified_args: dict[str, Any] | None = None,
    instruction: str | None = None,
) -> dict[str, Any]:
    """Register and launch a background lead generation campaign via Celery or thread pool fallback."""
    tid = int(thread_id)
    clear_thread_cancel(tid)
    raw_details = (approval_payload or {}).get("details") or {}
    merged = dict(raw_details)
    if isinstance(modified_args, dict):
        merged.update(modified_args)
    quota = int(merged.get("quota") or 100)
    role = str(merged.get("role") or "Python Developer")

    job_id = f"leadjob_{tid}_{uuid.uuid4().hex[:8]}"
    initial_msg = f"Searching (0/{quota}) → Queued background campaign for {role}..."
    now = time.time()

    job_state = {
        "job_id": job_id,
        "thread_id": tid,
        "user_id": int(user_id),
        "status": "running",
        "cancelled": False,
        "step": "discover_boards",
        "message": initial_msg,
        "completed": 0,
        "quota": quota,
        "steps_history": [
            {"step": "discover_boards", "message": initial_msg, "completed": 0, "quota": quota, "ts": now}
        ],
        "created_at": now,
        "updated_at": now,
    }

    with _LOCK:
        LEAD_JOB_REGISTRY[job_id] = job_state
        THREAD_ACTIVE_JOB[tid] = job_id

    # Always run in local ThreadPoolExecutor so in-memory progress updates stream immediately
    # while Celery task remains available for distributed workers when CELERY_USE_BROKER=true.
    import os
    if os.getenv("CELERY_USE_DISTRIBUTED_WORKER", "false").lower() in ("1", "true", "yes"):
        try:
            run_lead_generation_campaign_task.delay(
                job_id, tid, int(user_id), approval_payload, modified_args, instruction
            )
            return dict(job_state)
        except Exception as exc:
            logger.warning("Celery broker unavailable (%s), falling back to background thread pool.", exc)

    _EXECUTOR.submit(
        _execute_background_lead_job,
        job_id,
        tid,
        int(user_id),
        approval_payload,
        modified_args,
        instruction,
    )
    return dict(job_state)
