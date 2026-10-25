from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


MAX_CAMPAIGN_QUOTA = 100


class CampaignStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED_TARGET_MET = "COMPLETED_TARGET_MET"
    COMPLETED_SUPPLY_EXHAUSTED = "COMPLETED_SUPPLY_EXHAUSTED"
    PAUSED_RATE_LIMITED = "PAUSED_RATE_LIMITED"


class RoutingStatus(str, Enum):
    """Axis 1: Domain Mail Infrastructure Routing Evidence."""
    UNCHECKED = "UNCHECKED"
    MX_RESOLVED = "MX_RESOLVED"
    NO_MX_IMPLICIT_FALLBACK = "NO_MX_IMPLICIT_FALLBACK"
    NULL_MX = "NULL_MX"
    NXDOMAIN = "NXDOMAIN"
    MX_HOST_UNRESOLVABLE = "MX_HOST_UNRESOLVABLE"
    MX_TARGET_IS_CNAME = "MX_TARGET_IS_CNAME"
    IPV6_ONLY_UNREACHABLE = "IPV6_ONLY_UNREACHABLE"
    NO_MAIL_ROUTE = "NO_MAIL_ROUTE"
    DNS_TEMPORARY_FAILURE = "DNS_TEMPORARY_FAILURE"


class AttributionStatus(str, Enum):
    """Axis 2: Executive Contact Attribution Provenance."""
    PUBLISHED_DIRECT_MATCH = "PUBLISHED_DIRECT_MATCH"
    PUBLISHED_PAGE_CONFIRMED = "PUBLISHED_PAGE_CONFIRMED"
    SEARCH_SNIPPET_UNVALIDATED = "SEARCH_SNIPPET_UNVALIDATED"
    INFERRED_FORMAT_MATCH = "INFERRED_FORMAT_MATCH"
    INFERRED_CANDIDATE = "INFERRED_CANDIDATE"
    AMBIGUOUS_NEEDS_REVIEW = "AMBIGUOUS_NEEDS_REVIEW"
    NONE = "NONE"


class SmtpStatus(str, Enum):
    """Axis 3: Protocol Observation Evidence."""
    UNPROBED = "UNPROBED"
    SMTP_RCPT_ACCEPTED = "SMTP_RCPT_ACCEPTED"
    SMTP_RCPT_REJECTED = "SMTP_RCPT_REJECTED"
    SMTP_TEMP_DEFERRED = "SMTP_TEMP_DEFERRED"
    CATCH_ALL_SUSPECTED = "CATCH_ALL_SUSPECTED"
    PROBE_SKIPPED_SEG = "PROBE_SKIPPED_SEG"
    PROBE_BLOCKED_PORT25 = "PROBE_BLOCKED_PORT25"


class IdentityConfidence(str, Enum):
    CURRENT_CONFIRMED = "CURRENT_CONFIRMED"
    AMBIGUOUS_NEEDS_REVIEW = "AMBIGUOUS_NEEDS_REVIEW"
    UNRESOLVED = "UNRESOLVED"


class LeadGrade(str, Enum):
    GRADE_A = "GRADE_A"
    GRADE_B = "GRADE_B"
    GRADE_C = "GRADE_C"
    REVIEW_QUEUE = "REVIEW_QUEUE"


class ExportState(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ATSBoard:
    platform: str
    slug: str
    api_url: str
    careers_page_url: str | None = None


@dataclass(frozen=True)
class ATSJob:
    platform: str
    board_slug: str
    external_id: str
    title: str
    location: str
    absolute_url: str
    company_size: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    vacancy_is_live: bool = True
    company_domain_hint: str | None = None
    company_name_hint: str | None = None


@dataclass(frozen=True)
class JobLead:
    job: ATSJob
    company_name: str
    verified: bool = False
    verification_status: str = "UNVERIFIED"
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class CampaignCriteria:
    role: str
    locations: tuple[str, ...]
    quota: int = MAX_CAMPAIGN_QUOTA
    min_company_size: int | None = None
    max_company_size: int | None = 150
    max_discovery_waves: int = 3

    def __post_init__(self) -> None:
        # Enforce hard ceiling of 100 jobs/leads maximum
        clamped = max(0, min(int(self.quota), MAX_CAMPAIGN_QUOTA))
        object.__setattr__(self, "quota", clamped)
        if isinstance(self.locations, str):
            object.__setattr__(self, "locations", (self.locations,))
        else:
            object.__setattr__(self, "locations", tuple(self.locations))


@dataclass(frozen=True)
class GateResult:
    gate: str
    status: str
    records: tuple[Any, ...] = ()
    errors: tuple[str, ...] = ()
    completed_at: datetime | None = None


@dataclass
class CompanyRecord:
    canonical_domain: str
    canonical_name: str
    routing_status: RoutingStatus = RoutingStatus.UNCHECKED
    primary_mx_host: str | None = None
    all_mx_hosts: tuple[str, ...] = ()
    inferred_provider: str = "UNCLASSIFIED"
    provider_evidence: str | None = None
    catch_all_status: str = "UNKNOWN"
    observed_email_pattern: str | None = None
    phone_number: str | None = None
    phone_type: str = "HQ_SWITCHBOARD"
    dns_ttl: int = 3600
    retryable: bool = False
    dns_checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ExecutiveIdentity:
    full_name: str
    normalized_name: str
    title: str
    source_url: str
    confidence: IdentityConfidence
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    priority_rank: int = 99
    notes: str = ""


@dataclass(frozen=True)
class ContactObservation:
    normalized_email: str
    discovery_tier: str  # TIER_1_DOM, TIER_2_WEB_MENTION, TIER_3_PERMUTATION
    attribution_status: AttributionStatus
    source_url: str | None = None
    smtp_status: SmtpStatus = SmtpStatus.UNPROBED
    smtp_code: int | None = None
    smtp_enhanced_code: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LeadRecord:
    campaign_role: str
    location_bucket: str
    company: CompanyRecord
    job_title_signal: str
    job_url_signal: str
    vacancy_is_live: bool
    exec_full_name: str
    normalized_exec_name: str
    exec_title: str
    identity_source_url: str
    identity_observed_at: datetime
    identity_confidence: IdentityConfidence
    best_email: str | None
    attribution_status: AttributionStatus
    smtp_status: SmtpStatus
    lead_grade: LeadGrade
    linkedin_url: str = ""
    phone_number: str | None = None
    phone_type: str = "HQ_SWITCHBOARD"
    observations: tuple[ContactObservation, ...] = ()
    export_state: ExportState = ExportState.PENDING
    export_batch_id: str | None = None
