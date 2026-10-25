from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

import httpx

from .discovery import build_board_url, extract_ats_slug
from .models import ATSBoard, ATSJob, MAX_CAMPAIGN_QUOTA

ATS_SYSTEM_DOMAINS = {
    "lever.co",
    "jobs.lever.co",
    "greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "workable.com",
    "apply.workable.com",
    "breezy.hr",
}


def parse_ats_url(url: str) -> ATSBoard | None:
    platform, slug = extract_ats_slug(url)
    if not platform or not slug:
        return None
    return build_board_url(platform, slug)


def parse_ats_urls(urls: Iterable[str]) -> list[ATSBoard]:
    boards: list[ATSBoard] = []
    seen: set[tuple[str, str]] = set()
    for url in urls:
        board = parse_ats_url(url)
        if board and (board.platform, board.slug) not in seen:
            seen.add((board.platform, board.slug))
            boards.append(board)
    return boards


def _extract_domain_hint(item: dict[str, Any]) -> str | None:
    for key in ("company_url", "companyUrl", "website", "homepage"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            parsed = urlparse(val if "://" in val else f"https://{val}")
            host = (parsed.hostname or "").lower().removeprefix("www.")
            if host and not any(host == d or host.endswith(f".{d}") for d in ATS_SYSTEM_DOMAINS):
                return host
    return None


def fetch_board_jobs(
    board: ATSBoard,
    timeout: float = 15.0,
    max_jobs: int = MAX_CAMPAIGN_QUOTA,
) -> list[ATSJob]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DeterministicLeadEngine/4.1)",
        "Accept": "application/json",
    }
    response = httpx.get(board.api_url, headers=headers, timeout=timeout, follow_redirects=True)
    if response.status_code == 404:
        # Historical signal: company existed on ATS even though current feed is closed
        return []
    response.raise_for_status()
    jobs = normalize_board_jobs(board, response.json())
    return jobs[: max(0, min(max_jobs, MAX_CAMPAIGN_QUOTA))]


def normalize_board_jobs(board: ATSBoard, payload: Any) -> list[ATSJob]:
    company_name_hint: str | None = None
    company_domain_hint: str | None = None

    if board.platform == "lever":
        items = payload if isinstance(payload, list) else []
    elif isinstance(payload, dict):
        company_name_hint = (
            payload.get("name")
            or payload.get("company_name")
            or payload.get("organizationName")
        )
        company_domain_hint = _extract_domain_hint(payload)
        items = (
            payload.get("jobs")
            or payload.get("positions")
            or payload.get("jobPostings")
            or []
        )
    else:
        items = []

    jobs: list[ATSJob] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        categories = item.get("categories")
        location = categories.get("location") if isinstance(categories, dict) else None
        if not location and isinstance(item.get("location"), dict):
            location = item["location"].get("name") or item["location"].get("locationName")

        domain_hint = _extract_domain_hint(item) or company_domain_hint
        name_hint = str(item.get("company_name") or company_name_hint or board.slug).strip()

        jobs.append(
            ATSJob(
                platform=board.platform,
                board_slug=board.slug,
                external_id=str(
                    item.get("id")
                    or item.get("posting_id")
                    or item.get("slug")
                    or item.get("shortcode")
                    or ""
                ),
                title=str(item.get("text") or item.get("title") or item.get("name") or ""),
                location=str(location or item.get("location") or item.get("location_name") or ""),
                absolute_url=str(
                    item.get("hostedUrl")
                    or item.get("absolute_url")
                    or item.get("url")
                    or item.get("jobUrl")
                    or board.careers_page_url
                    or ""
                ),
                company_size=str(
                    item.get("company_size")
                    or item.get("companySize")
                    or item.get("team_size")
                    or ""
                )
                or None,
                raw=item,
                vacancy_is_live=True,
                company_domain_hint=domain_hint,
                company_name_hint=name_hint,
            )
        )
    return jobs
