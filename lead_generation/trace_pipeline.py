"""
Comprehensive Step-by-Step Diagnostic Tracer for Lead Generation Pipeline.
Traces every gate in execution, prints why candidate jobs/companies pass or fail,
and provides full visibility into the lead funnel bottlenecks.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

# Ensure project root is in sys.path and load its environment explicitly.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

# Ensure .env is explicitly loaded
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from lead_generation.ats import fetch_board_jobs, parse_ats_urls
from lead_generation.discovery import (
    CIRCUIT_BREAKERS,
    build_board_url,
    build_composite_search_dorks,
    build_search_dorks,
    discover_company_slugs,
    extract_ats_slug,
    get_curated_seeds,
    search_web,
)
from lead_generation.domain_resolver import resolve_company_entity
from lead_generation.filtering import filter_jobs, parse_company_size_bounds
from lead_generation.identity import resolve_executive_identity, run_contact_waterfall
from lead_generation.models import (
    ATSBoard,
    ATSJob,
    AttributionStatus,
    CampaignCriteria,
    CompanyRecord,
    ExportState,
    LeadRecord,
    MAX_CAMPAIGN_QUOTA,
    RoutingStatus,
    SmtpStatus,
)
from lead_generation.phone_extractor import resolve_company_phone
from lead_generation.verification import compute_lead_grade, probe_company_candidates, select_best_observation


def run_traced_pipeline(
    role: str = "Software Engineer",
    locations: tuple[str, ...] = ("US", "UK", "Remote"),
    quota: int = 100,
    company_size: str = "10-150",
    require_linkedin: bool = True,
    sample_limit: int = 15,
) -> None:
    print("=" * 90)
    print("🔬 LEAD GENERATION PIPELINE — COMPREHENSIVE STEP-BY-STEP DIAGNOSTIC TRACE")
    print("=" * 90)
    print(f"Target Role:       '{role}'")
    print(f"Target Locations:  {locations}")
    print(f"Requested Quota:   {quota} (Hard Ceiling = {MAX_CAMPAIGN_QUOTA})")
    print(f"Company Size:      '{company_size}'")
    print(f"Require LinkedIn:  {require_linkedin} (NON-NEGOTIABLE)")
    print("-" * 90)

    # 0. Check Search API Keys
    print("\n[Step 0: Checking Search Engine & Environment Configuration]")
    serper_key = os.getenv("SERPER_API_KEY")
    tavily_key = os.getenv("TAVILY_API_KEY")
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY")
    google_key = os.getenv("GOOGLE_API_KEY")
    print(f"  • SERPER_API_KEY:         {'CONFIGURED (Google Search 2.5k queries)' if serper_key else 'MISSING'}")
    print(f"  • TAVILY_API_KEY:         {'CONFIGURED' if tavily_key else 'MISSING'}")
    print(f"  • BRAVE_SEARCH_API_KEY:   {'CONFIGURED' if brave_key else 'MISSING'}")
    print(f"  • GOOGLE_API_KEY:         {'CONFIGURED' if google_key else 'MISSING'}")

    # 1. Trace Gate 0: Discovery
    print("\n" + "=" * 90)
    print("[Gate 0: ATS Company Discovery & Board Slugs Collection]")
    print("=" * 90)
    
    bounds = parse_company_size_bounds(company_size)
    min_size = bounds[0] if bounds else 10
    max_size = bounds[1] if bounds else 150
    criteria = CampaignCriteria(
        role=role,
        locations=locations,
        quota=quota,
        min_company_size=min_size,
        max_company_size=max_size,
    )

    all_discovered_slugs: set[tuple[str, str]] = set()
    for loc in locations:
        print(f"\n  🔍 Discovering ATS seeds for Location: '{loc}'...")
        curated = get_curated_seeds(loc)
        print(f"     - Curated Seed Catalog has: {len(curated)} seeds for '{loc}'")
        
        # Test Live Search Queries
        dorks = build_composite_search_dorks(role, loc)
        print(f"     - Running {len(dorks)} live composite search dorks...")
        live_found = 0
        for dork in dorks:
            try:
                results = search_web(dork, max_results=20)
                extracted = [extract_ats_slug(r.get("href", "")) for r in results]
                valid_extracted = [x for x in extracted if x[0] and x[1]]
                live_found += len(valid_extracted)
                print(f"       • Dork: {dork[:65]}... -> {len(results)} SERP results, {len(valid_extracted)} ATS slugs")
            except Exception as e:
                print(f"       • Dork: {dork[:65]}... -> FAILED ({e})")
        
        loc_seeds = discover_company_slugs(role, loc, max_results=max(50, quota))
        print(f"     => Total unique candidate company boards for '{loc}': {len(loc_seeds)}")
        all_discovered_slugs.update(loc_seeds)

    print(f"\n  ✓ Total Distinct ATS Company Boards Discovered Across All Locations: {len(all_discovered_slugs)}")
    if len(all_discovered_slugs) < quota:
        print(f"  ⚠️  BOTTLENECK WARNING: Discovered candidate companies ({len(all_discovered_slugs)}) is ALREADY LESS than requested quota ({quota})!")
        print("      Because '1 Lead = 1 Unique Company' is enforced, the output can NEVER exceed this number.")

    # 2. Ingest ATS Job Feeds
    print("\n" + "=" * 90)
    print(f"[Gate 1: Ingesting ATS Job Feeds from {len(all_discovered_slugs)} Candidate Boards]")
    print("=" * 90)

    boards = [build_board_url(p, s) for p, s in all_discovered_slugs]
    boards = [b for b in boards if b]

    all_jobs: list[ATSJob] = []
    board_status_counts = Counter()

    print(f"  Fetching jobs from {len(boards)} boards (sampling trace of first 10)...")
    for idx, board in enumerate(boards, start=1):
        try:
            b_jobs = fetch_board_jobs(board, timeout=8.0, max_jobs=MAX_CAMPAIGN_QUOTA)
            if b_jobs:
                board_status_counts["SUCCESS_WITH_JOBS"] += 1
                all_jobs.extend(b_jobs)
                if idx <= 10 or idx % 20 == 0:
                    print(f"    [{idx:03d}/{len(boards)}] {board.platform.upper():10} {board.slug:25} -> {len(b_jobs)} jobs found")
            else:
                board_status_counts["ZERO_OPEN_JOBS"] += 1
                if idx <= 10:
                    print(f"    [{idx:03d}/{len(boards)}] {board.platform.upper():10} {board.slug:25} -> 0 active jobs (board feed empty)")
        except Exception as e:
            board_status_counts["FETCH_ERROR"] += 1
            if idx <= 10:
                print(f"    [{idx:03d}/{len(boards)}] {board.platform.upper():10} {board.slug:25} -> ERROR: {e}")

    print(f"\n  ✓ Total Raw ATS Jobs Ingested: {len(all_jobs)}")
    print(f"  • Boards yielding active jobs: {board_status_counts['SUCCESS_WITH_JOBS']}")
    print(f"  • Boards with 0 active jobs:   {board_status_counts['ZERO_OPEN_JOBS']}")
    print(f"  • Boards failing network/API:  {board_status_counts['FETCH_ERROR']}")

    # 3. Filter Vacancies by Role & Headcount
    print("\n" + "=" * 90)
    print(f"[Gate 1B: Filtering {len(all_jobs)} Jobs by Role ('{role}') & Headcount ('{company_size}')]")
    print("=" * 90)

    matching_jobs_by_loc: dict[str, list[ATSJob]] = {}
    for loc in locations:
        m_jobs = filter_jobs(
            all_jobs,
            role=role,
            location=loc,
            min_company_size=min_size,
            max_company_size=max_size,
        )
        matching_jobs_by_loc[loc] = m_jobs
        print(f"  • Location '{loc}': {len(m_jobs)} jobs matched role '{role}'")

    # Combine and deduplicate jobs across locations
    selected_jobs: list[ATSJob] = []
    seen_job_keys: set[tuple[str, str, str, str]] = set()
    for loc, j_list in matching_jobs_by_loc.items():
        for j in j_list:
            key = (j.platform, j.board_slug, j.external_id, j.absolute_url)
            if key not in seen_job_keys:
                seen_job_keys.add(key)
                selected_jobs.append(j)

    print(f"\n  ✓ Total Deduplicated Jobs Matching Role: {len(selected_jobs)}")
    
    # Group by company board
    jobs_by_board: dict[tuple[str, str], list[ATSJob]] = {}
    for job in selected_jobs:
        jobs_by_board.setdefault((job.platform, job.board_slug), []).append(job)

    print(f"  ✓ Total Distinct Companies with Active Matching Jobs: {len(jobs_by_board)}")
    if len(jobs_by_board) < quota:
        print(f"  ⚠️  CRITICAL BOTTLENECK: Only {len(jobs_by_board)} distinct companies have matching '{role}' jobs right now!")

    # 4. Gates 2–5: Company-by-Company Verification Trace
    print("\n" + "=" * 90)
    print(f"[Gates 2–5: Tracing Company Verification & LinkedIn Identity Resolution]")
    print(f"Processing candidate companies (showing trace up to first {sample_limit})...")
    print("=" * 90)

    surviving_leads: list[LeadRecord] = []
    dropped_summary = Counter()

    for idx, ((platform, slug), b_jobs) in enumerate(jobs_by_board.items(), start=1):
        board = build_board_url(platform, slug)
        primary_job = b_jobs[0]
        
        # Gate 2: Domain Resolution
        company = resolve_company_entity(board, b_jobs)
        
        # Check MX routing
        if company.routing_status in (
            RoutingStatus.NXDOMAIN,
            RoutingStatus.NULL_MX,
            RoutingStatus.MX_HOST_UNRESOLVABLE,
            RoutingStatus.NO_MAIL_ROUTE,
        ):
            dropped_summary[f"GATE_2_UNROUTABLE_DOMAIN ({company.routing_status.value})"] += 1
            if idx <= sample_limit:
                print(f"\n[{idx:02d}] ❌ {slug} ({company.canonical_domain}) -> DROPPED at Gate 2: DNS/MX unroutable ({company.routing_status.value})")
            continue

        # Gate 3: Executive Identity & LinkedIn URL
        start_id = time.perf_counter()
        executive = resolve_executive_identity(company, enable_site_scrape=True)
        dur_id = time.perf_counter() - start_id
        
        has_linkedin = bool(executive.source_url and "linkedin.com/in/" in executive.source_url)
        is_unresolved = executive.full_name == "Unresolved Executive"

        if require_linkedin and (not has_linkedin or is_unresolved):
            dropped_summary["GATE_3_MISSING_LINKEDIN_URL"] += 1
            if idx <= sample_limit:
                print(f"\n[{idx:02d}] ❌ {company.canonical_name} ({company.canonical_domain}) -> DROPPED at Gate 3: No verified LinkedIn profile found! ({dur_id:.2f}s)")
                print(f"       Executive: '{executive.full_name}', Notes: {executive.notes}")
            continue

        # Gate 4 & 5: Contact Waterfall & Verification
        phone_num, phone_type = resolve_company_phone(
            company,
            job_payloads=[j.raw for j in b_jobs if j.raw],
            default_country=primary_job.location or locations[0],
            enable_dom_scrape=False,
        )

        waterfall_obs = run_contact_waterfall(company, executive, enable_dom_scrape=False, enable_web_mentions=True)
        probed_obs = probe_company_candidates(company, waterfall_obs, enable_live_smtp=False)
        best_obs = select_best_observation(probed_obs)

        best_email = best_obs.normalized_email if best_obs else f"{executive.normalized_name.replace(' ', '.')}@{company.canonical_domain}"
        attr_status = best_obs.attribution_status if best_obs else AttributionStatus.INFERRED_CANDIDATE
        smtp_status = best_obs.smtp_status if best_obs else SmtpStatus.PROBE_BLOCKED_PORT25

        grade = compute_lead_grade(
            routing_status=company.routing_status,
            identity_confidence=executive.confidence,
            attribution_status=attr_status,
            smtp_status=smtp_status,
        )

        lead = LeadRecord(
            campaign_role=role,
            location_bucket=primary_job.location or locations[0],
            company=company,
            job_title_signal=primary_job.title,
            job_url_signal=primary_job.absolute_url,
            vacancy_is_live=True,
            exec_full_name=executive.full_name,
            normalized_exec_name=executive.normalized_name,
            exec_title=executive.title,
            identity_source_url=executive.source_url,
            identity_observed_at=datetime.now(timezone.utc),
            identity_confidence=executive.confidence,
            best_email=best_email,
            attribution_status=attr_status,
            smtp_status=smtp_status,
            lead_grade=grade,
            linkedin_url=executive.source_url,
            phone_number=phone_num,
            phone_type=phone_type,
            observations=tuple(probed_obs),
            export_state=ExportState.PENDING,
        )
        surviving_leads.append(lead)

        if idx <= sample_limit or len(surviving_leads) <= 5:
            print(f"\n[{idx:02d}] ✅ VERIFIED LEAD #{len(surviving_leads)}: {company.canonical_name} ({company.canonical_domain})")
            print(f"       Executive: {lead.exec_full_name} ({lead.exec_title})")
            print(f"       LinkedIn:  {lead.linkedin_url}")
            print(f"       Email:     {lead.best_email} [{lead.lead_grade.value}]")
            print(f"       Job Signal: {lead.job_title_signal}")

        if len(surviving_leads) >= quota:
            print(f"\n  🎯 Quota target ({quota}) met! Breaking early.")
            break

    # 5. Diagnostic Funnel Summary
    print("\n" + "=" * 90)
    print("📊 FULL FUNNEL DIAGNOSTIC SUMMARY")
    print("=" * 90)
    print(f"  1. Candidate ATS Company Boards Discovered:      {len(all_discovered_slugs)}")
    print(f"  2. Ingested Active ATS Vacancies:                {len(all_jobs)}")
    print(f"  3. Vacancies Matching Role ('{role}'):           {len(selected_jobs)}")
    print(f"  4. Distinct Companies with Active Matching Jobs: {len(jobs_by_board)}")
    print(f"  5. Dropped at Gate 2 (Unroutable MX / NXDOMAIN): {sum(v for k, v in dropped_summary.items() if 'GATE_2' in k)}")
    print(f"  6. Dropped at Gate 3 (Missing LinkedIn Profile): {dropped_summary.get('GATE_3_MISSING_LINKEDIN_URL', 0)}")
    print(f"  -------------------------------------------------------------")
    print(f"  🏆 FINAL VERIFIED DISTINCT LEADS GENERATED:     {len(surviving_leads)} / {quota}")
    print("=" * 90)


if __name__ == "__main__":
    run_traced_pipeline()
