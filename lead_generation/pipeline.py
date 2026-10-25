from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from .ats import fetch_board_jobs, parse_ats_urls
from .discovery import build_board_url, build_search_dorks, discover_company_slugs
from .domain_resolver import resolve_company_entity
from .exporting import TransactionalOutbox, partition_leads_by_grade
from .filtering import filter_jobs
from .identity import resolve_executive_identity, run_contact_waterfall
from .phone_extractor import resolve_company_phone
from .models import (
    ATSBoard,
    ATSJob,
    AttributionStatus,
    CampaignCriteria,
    CampaignStatus,
    CompanyRecord,
    ExportState,
    LeadRecord,
    MAX_CAMPAIGN_QUOTA,
    RoutingStatus,
    SmtpStatus,
)
from .verification import (
    compute_lead_grade,
    probe_company_candidates,
    select_best_observation,
)


class LeadGenerationPipeline:
    def __init__(
        self,
        search_slugs: Callable[[str, str, int], set[tuple[str, str]]] = discover_company_slugs,
        outbox: TransactionalOutbox | None = None,
    ) -> None:
        self.search_slugs = search_slugs
        self.outbox = outbox or TransactionalOutbox()

    @staticmethod
    def _allocate_regional_quotas(locations: tuple[str, ...], total_quota: int) -> dict[str, int]:
        """Gate 0: Splits total quota (capped at 100) evenly across target locations."""
        capped_total = max(0, min(total_quota, MAX_CAMPAIGN_QUOTA))
        if not locations or capped_total == 0:
            return {}
        n_locs = len(locations)
        base = capped_total // n_locs
        remainder = capped_total % n_locs
        return {
            loc: base + (1 if idx < remainder else 0)
            for idx, loc in enumerate(locations)
        }

    def discover(
        self,
        criteria: CampaignCriteria,
        ats_urls: Iterable[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """
        Gates 0 & 1: Discovers ATS boards, fetches structured postings, filters by role,
        location, and company size (<150), and enforces the hard 100-job ceiling.
        """
        effective_quota = max(0, min(criteria.quota, MAX_CAMPAIGN_QUOTA))
        regional_quotas = self._allocate_regional_quotas(criteria.locations, effective_quota)

        if progress_callback:
            progress_callback({
                "step": "discover_boards",
                "message": f"Searching startup ATS job boards across {', '.join(criteria.locations)}… (0/{effective_quota})",
                "completed": 0,
                "quota": effective_quota,
            })

        boards: list[ATSBoard] = []
        if ats_urls is not None:
            boards = parse_ats_urls(ats_urls)
        else:
            for location in criteria.locations:
                if should_cancel and should_cancel():
                    break
                seeds = self.search_slugs(criteria.role, location, max(50, effective_quota))
                for platform, slug in sorted(seeds):
                    board = build_board_url(platform, slug)
                    if board:
                        boards.append(board)

        unique_boards = {(board.platform, board.slug): board for board in boards}
        boards = list(unique_boards.values())

        jobs: list[ATSJob] = []
        errors: list[dict[str, str]] = []
        for idx, board in enumerate(boards, 1):
            if should_cancel and should_cancel():
                break
            if progress_callback and (idx == 1 or idx % 5 == 0 or idx == len(boards)):
                progress_callback({
                    "step": "fetch_boards",
                    "message": f"Ingesting ATS job signals ({idx}/{len(boards)} boards: {board.slug})… (0/{effective_quota})",
                    "completed": 0,
                    "quota": effective_quota,
                })
            try:
                jobs.extend(fetch_board_jobs(board, max_jobs=MAX_CAMPAIGN_QUOTA))
            except Exception as exc:
                errors.append(
                    {"board": board.slug, "platform": board.platform, "error": str(exc)}
                )

        # Gate 0 Regional Quota Balancing with shortfall redistribution
        selected_jobs: list[ATSJob] = []
        seen_job_keys: set[tuple[str, str, str, str]] = set()
        remaining_candidates: list[ATSJob] = []

        for location in criteria.locations:
            loc_matches = filter_jobs(
                jobs,
                criteria.role,
                location,
                min_company_size=criteria.min_company_size,
                max_company_size=criteria.max_company_size,
            )
            loc_quota = regional_quotas.get(location, effective_quota)
            loc_added = 0

            # Pass 1: Prioritize 1 job per distinct board to maximize company diversity
            board_seen: set[str] = set()
            pass1 = []
            pass2 = []
            for job in loc_matches:
                if job.board_slug not in board_seen:
                    board_seen.add(job.board_slug)
                    pass1.append(job)
                else:
                    pass2.append(job)

            for job in pass1 + pass2:
                key = (job.platform, job.board_slug, job.external_id, job.absolute_url)
                if key in seen_job_keys:
                    continue
                seen_job_keys.add(key)
                if loc_added < loc_quota:
                    selected_jobs.append(job)
                    loc_added += 1
                else:
                    remaining_candidates.append(job)

        # Fill buffer from remaining matches up to 2x quota or max available
        supply_ceiling = max(effective_quota, min(len(jobs), MAX_CAMPAIGN_QUOTA * 2))
        for job in remaining_candidates:
            if len(selected_jobs) >= supply_ceiling:
                break
            selected_jobs.append(job)

        if progress_callback:
            progress_callback({
                "step": "filter_vacancies",
                "message": f"Matched {len(selected_jobs)} live '{criteria.role}' vacancies across {len(boards)} startups… (0/{effective_quota})",
                "completed": 0,
                "quota": effective_quota,
            })

        dorks = {
            location: build_search_dorks(criteria.role, location)
            for location in criteria.locations
        }
        campaign_status = (
            CampaignStatus.COMPLETED_TARGET_MET
            if len(selected_jobs) >= effective_quota and effective_quota > 0
            else CampaignStatus.COMPLETED_SUPPLY_EXHAUSTED
        )
        return {
            "criteria": criteria,
            "regional_quotas": regional_quotas,
            "dorks": dorks,
            "boards": boards,
            "jobs": selected_jobs,
            "errors": errors,
            "status": campaign_status.value,
            "discovery_required": not boards,
        }

    def run_campaign(
        self,
        criteria: CampaignCriteria,
        ats_urls: Iterable[str] | None = None,
        enable_dom_scrape: bool = False,
        enable_web_mentions: bool = True,
        enable_live_smtp: bool = False,
        require_linkedin: bool = True,
        persist_outbox: bool = True,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """
        Executes the complete 7-Gate Deterministic Waterfall end-to-end:
        Gate 0: Adaptive Regional Quota Planner
        Gate 1: ATS Signal Ingestion & Deduplication
        Gate 2A: Canonical Domain Resolution (with split-domain recovery)
        Gate 2B: Audited RFC 5321/7505 DNS Router
        Gate 3: Zero-Login Executive Identity Resolution (enforcing LinkedIn profile)
        Gate 4: 3-Tier Contact Waterfall & Phone Sweet-Spot Extractor
        Gate 5: Canary & SMTP Observation + 3-Axis Epistemic Lead Grader
        Gate 6: Transactional Outbox Persistence & Deliverable Tab Partitioning
        """
        discovery_data = self.discover(
            criteria,
            ats_urls=ats_urls,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )
        jobs: list[ATSJob] = discovery_data["jobs"]
        boards_by_key = {(b.platform, b.slug): b for b in discovery_data["boards"]}

        # Group jobs by (platform, board_slug) so each company is resolved cleanly
        jobs_by_board: dict[tuple[str, str], list[ATSJob]] = {}
        for job in jobs:
            jobs_by_board.setdefault((job.platform, job.board_slug), []).append(job)

        leads: list[LeadRecord] = []
        seen_domains: set[str] = set()
        dropped_companies: list[dict[str, Any]] = []

        target_quota = min(criteria.quota, MAX_CAMPAIGN_QUOTA)
        evaluated_count = 0

        # Enforce 1 Lead = 1 Unique Company (never duplicate the same company/CEO across rows)
        for (platform, slug), board_jobs in jobs_by_board.items():
            if len(leads) >= target_quota:
                break
            if should_cancel and should_cancel():
                break

            board = boards_by_key.get((platform, slug)) or build_board_url(platform, slug)
            if not board:
                continue

            evaluated_count += 1
            if progress_callback:
                progress_callback({
                    "step": "searching_company",
                    "message": f"Searching ({len(leads)}/{target_quota}) → Resolving domain & DNS MX for {slug}…",
                    "completed": len(leads),
                    "quota": target_quota,
                    "evaluated": evaluated_count,
                })

            # Gate 2A & 2B: Resolve canonical domain and mail routing
            company: CompanyRecord = resolve_company_entity(board, board_jobs)
            if company.canonical_domain in seen_domains:
                continue
            seen_domains.add(company.canonical_domain)

            # Drop unroutable domains
            if company.routing_status in (
                RoutingStatus.NXDOMAIN,
                RoutingStatus.NULL_MX,
                RoutingStatus.MX_HOST_UNRESOLVABLE,
                RoutingStatus.NO_MAIL_ROUTE,
            ):
                dropped_companies.append(
                    {
                        "slug": slug,
                        "domain": company.canonical_domain,
                        "routing_status": company.routing_status.value,
                    }
                )
                continue

            if progress_callback:
                progress_callback({
                    "step": "resolving_identity",
                    "message": f"Searching ({len(leads)}/{target_quota}) → Finding verified CEO/CTO LinkedIn for {company.canonical_name} ({company.canonical_domain})…",
                    "completed": len(leads),
                    "quota": target_quota,
                    "evaluated": evaluated_count,
                })

            # Gate 3: Executive Identity Resolution
            executive = resolve_executive_identity(company, enable_site_scrape=enable_dom_scrape)

            # Enforce LinkedIn Must Have
            linkedin_url = executive.source_url if "linkedin.com/in/" in executive.source_url else ""
            if require_linkedin and (not linkedin_url or executive.full_name == "Unresolved Executive"):
                dropped_companies.append(
                    {
                        "slug": slug,
                        "domain": company.canonical_domain,
                        "reason": "MISSING_LINKEDIN_URL",
                    }
                )
                continue

            if progress_callback:
                progress_callback({
                    "step": "verifying_mail",
                    "message": f"Verifying mail & E.164 phone ({len(leads)}/{target_quota}) → {executive.full_name} @ {company.canonical_domain}…",
                    "completed": len(leads),
                    "quota": target_quota,
                    "evaluated": evaluated_count,
                })

            # Extract Phone Number (Sweet Spot)
            primary_job = board_jobs[0]
            phone_num, phone_type = resolve_company_phone(
                company,
                job_payloads=[j.raw for j in board_jobs if j.raw],
                default_country=primary_job.location or criteria.locations[0],
                enable_dom_scrape=enable_dom_scrape,
            )

            # Gate 4: 3-Tier Contact Waterfall
            raw_observations = run_contact_waterfall(
                company,
                executive,
                enable_dom_scrape=enable_dom_scrape,
                enable_web_mentions=enable_web_mentions,
            )

            # Gate 5: Canary & SMTP Observation + 3-Axis Grading
            probed_observations = probe_company_candidates(
                company,
                raw_observations,
                enable_live_smtp=enable_live_smtp,
            )
            best_obs = select_best_observation(probed_observations)

            attr_status = best_obs.attribution_status if best_obs else AttributionStatus.NONE
            smtp_status = best_obs.smtp_status if best_obs else SmtpStatus.UNPROBED
            best_email = best_obs.normalized_email if best_obs else None

            grade = compute_lead_grade(
                routing_status=company.routing_status,
                identity_confidence=executive.confidence,
                attribution_status=attr_status,
                smtp_status=smtp_status,
            )

            extra_roles_count = len(board_jobs) - 1
            job_signal = (
                f"{primary_job.title} (+{extra_roles_count} more open roles)"
                if extra_roles_count > 0
                else primary_job.title
            )

            lead = LeadRecord(
                campaign_role=criteria.role,
                location_bucket=primary_job.location or criteria.locations[0],
                company=company,
                job_title_signal=job_signal,
                job_url_signal=primary_job.absolute_url,
                vacancy_is_live=primary_job.vacancy_is_live,
                exec_full_name=executive.full_name,
                normalized_exec_name=executive.normalized_name,
                exec_title=executive.title,
                identity_source_url=executive.source_url,
                identity_observed_at=executive.observed_at,
                identity_confidence=executive.confidence,
                best_email=best_email,
                attribution_status=attr_status,
                smtp_status=smtp_status,
                lead_grade=grade,
                linkedin_url=linkedin_url,
                phone_number=phone_num,
                phone_type=phone_type,
                observations=tuple(probed_observations),
                export_state=ExportState.PENDING,
            )
            leads.append(lead)

            # Gate 6: Transactional Outbox Persistence
            if persist_outbox:
                self.outbox.save_lead_idempotent(lead)

        tabs = partition_leads_by_grade(leads)
        return {
            **discovery_data,
            "leads": leads[:MAX_CAMPAIGN_QUOTA],
            "tabs": tabs,
            "dropped_companies": dropped_companies,
            "completed_at": datetime.now(timezone.utc),
        }

