"""Backward-compatible facade for the modular 7-Gate lead-generation package."""

from __future__ import annotations

from collections.abc import Iterable

from .ats import fetch_board_jobs, normalize_board_jobs, parse_ats_url, parse_ats_urls
from .discovery import (
    build_composite_search_dorks,
    build_search_dorks,
    discover_company_slugs,
    extract_ats_slug,
    search_web,
)
from .domain_resolver import (
    normalize_canonical_domain,
    resolve_canonical_domain,
    resolve_company_entity,
    resolve_domain_mail_routing,
)
from .exporting import CsvExporter, JsonLinesExporter, TransactionalOutbox, partition_leads_by_grade
from .identity import (
    generate_executive_permutations,
    is_role_or_generic_email,
    parse_linkedin_snippet,
    resolve_executive_identity,
    run_contact_waterfall,
)
from .models import (
    ATSBoard,
    ATSJob,
    AttributionStatus,
    CampaignCriteria,
    CompanyRecord,
    ContactObservation,
    ExecutiveIdentity,
    IdentityConfidence,
    LeadGrade,
    LeadRecord,
    MAX_CAMPAIGN_QUOTA,
    RoutingStatus,
    SmtpStatus,
)
from .pipeline import LeadGenerationPipeline
from .verification import compute_lead_grade, probe_company_candidates

__all__ = [
    "ATSBoard",
    "ATSJob",
    "AttributionStatus",
    "CampaignCriteria",
    "CompanyRecord",
    "ContactObservation",
    "CsvExporter",
    "ExecutiveIdentity",
    "IdentityConfidence",
    "JsonLinesExporter",
    "LeadGenerationPipeline",
    "LeadGrade",
    "LeadRecord",
    "MAX_CAMPAIGN_QUOTA",
    "RoutingStatus",
    "SmtpStatus",
    "TransactionalOutbox",
    "build_composite_search_dorks",
    "build_search_dorks",
    "compute_lead_grade",
    "discover_company_slugs",
    "discover_jobs",
    "extract_ats_slug",
    "fetch_board_jobs",
    "generate_executive_permutations",
    "is_role_or_generic_email",
    "normalize_board_jobs",
    "normalize_canonical_domain",
    "parse_ats_url",
    "parse_ats_urls",
    "parse_linkedin_snippet",
    "partition_leads_by_grade",
    "probe_company_candidates",
    "resolve_canonical_domain",
    "resolve_company_entity",
    "resolve_domain_mail_routing",
    "resolve_executive_identity",
    "run_contact_waterfall",
    "run_lead_campaign",
    "search_web",
]


def discover_jobs(
    role: str,
    location: str | Iterable[str],
    ats_urls: Iterable[str] | None = None,
    limit: int = MAX_CAMPAIGN_QUOTA,
) -> dict:
    locations = (location,) if isinstance(location, str) else tuple(location)
    capped_limit = max(0, min(limit, MAX_CAMPAIGN_QUOTA))
    criteria = CampaignCriteria(role=role, locations=locations, quota=capped_limit)
    pipeline = LeadGenerationPipeline()
    return pipeline.discover(criteria, ats_urls=ats_urls)


def run_lead_campaign(
    role: str,
    location: str | Iterable[str],
    ats_urls: Iterable[str] | None = None,
    limit: int = MAX_CAMPAIGN_QUOTA,
    enable_live_smtp: bool = False,
    company_size: str | None = None,
) -> dict:
    from .filtering import parse_company_size_bounds

    locations = (location,) if isinstance(location, str) else tuple(location)
    capped_limit = max(0, min(limit, MAX_CAMPAIGN_QUOTA))
    bounds = parse_company_size_bounds(company_size) if company_size else None
    criteria = CampaignCriteria(
        role=role,
        locations=locations,
        quota=capped_limit,
        min_company_size=bounds[0] if bounds else None,
        max_company_size=bounds[1] if bounds else 150,
    )
    pipeline = LeadGenerationPipeline()
    return pipeline.run_campaign(
        criteria,
        ats_urls=ats_urls,
        enable_live_smtp=enable_live_smtp,
    )
