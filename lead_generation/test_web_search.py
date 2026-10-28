import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from lead_generation.ats import parse_ats_url, parse_ats_urls
from lead_generation.discovery import (
    CircuitBreaker,
    build_composite_search_dorks,
    build_search_dorks,
    discover_company_slugs,
    extract_ats_slug,
    get_cached_search,
    search_web,
    set_cached_search,
)
from lead_generation.domain_resolver import (
    extract_domain_from_ats_html,
    generate_split_domain_candidates,
    infer_edge_provider,
    normalize_canonical_domain,
    resolve_canonical_domain,
    resolve_domain_mail_routing,
)
from lead_generation.exporting import TransactionalOutbox, partition_leads_by_grade
from lead_generation.filtering import filter_jobs
from lead_generation.identity import (
    generate_executive_permutations,
    infer_email_pattern_from_samples,
    is_role_or_generic_email,
    normalize_person_name,
    parse_linkedin_snippet,
)
from lead_generation.models import (
    ATSJob,
    AttributionStatus,
    CampaignCriteria,
    CompanyRecord,
    ContactObservation,
    IdentityConfidence,
    LeadGrade,
    MAX_CAMPAIGN_QUOTA,
    RoutingStatus,
    SmtpStatus,
)
from lead_generation.pipeline import LeadGenerationPipeline
from lead_generation.verification import compute_lead_grade, probe_company_candidates


class DeterministicLeadGenTestSuite(unittest.TestCase):
    # ------------------------------------------------------------------
    # Gate 0 & 1: Discovery, Dorks, 100-Job Hard Limit & Reserved Slugs
    # ------------------------------------------------------------------
    def test_lever_url(self):
        board = parse_ats_url("https://jobs.lever.co/skyralio/b19283-python-engineer")
        self.assertEqual(board.platform, "lever")
        self.assertEqual(board.slug, "skyralio")
        self.assertEqual(
            board.api_url,
            "https://api.lever.co/v0/postings/skyralio?mode=json",
        )

    def test_greenhouse_url(self):
        board = parse_ats_url("https://boards.greenhouse.io/monzo/jobs/456789")
        self.assertEqual(board.platform, "greenhouse")
        self.assertEqual(board.slug, "monzo")
        self.assertEqual(
            board.api_url,
            "https://boards-api.greenhouse.io/v1/boards/monzo/jobs",
        )

    def test_ashby_workable_and_breezy_urls(self):
        urls = [
            "https://jobs.ashbyhq.com/acme/a123",
            "https://apply.workable.com/acme",
            "https://acme.breezy.hr/p/123",
        ]
        boards = parse_ats_urls(urls)
        self.assertEqual([board.platform for board in boards], ["ashby", "workable", "breezy"])
        self.assertEqual([board.slug for board in boards], ["acme", "acme", "acme"])

    def test_duplicate_boards_are_removed(self):
        boards = parse_ats_urls([
            "https://jobs.lever.co/skyralio/job-one",
            "https://jobs.lever.co/skyralio/job-two",
        ])
        self.assertEqual(len(boards), 1)

    def test_unrecognized_and_reserved_urls_rejected(self):
        self.assertIsNone(parse_ats_url("https://example.com/jobs/python-engineer"))
        self.assertEqual(extract_ats_slug("https://jobs.lever.co/search"), (None, None))
        self.assertEqual(extract_ats_slug("https://boards.greenhouse.io/about"), (None, None))
        self.assertEqual(extract_ats_slug("https://jobs.ashbyhq.com/privacy"), (None, None))
        self.assertEqual(extract_ats_slug("https://apply.workable.com/api"), (None, None))

    def test_search_dorks_and_uk_alias(self):
        dorks = build_search_dorks('Python "Developer"', "UK")
        self.assertEqual(
            dorks["lever"],
            'site:jobs.lever.co "Python Developer" "United Kingdom"',
        )
        comp = build_composite_search_dorks("Python", "AU")
        self.assertEqual(len(comp), 2)
        self.assertIn("Australia", comp[0])

    def test_hard_quota_clamped_to_100(self):
        criteria = CampaignCriteria(role="Python Developer", locations=("UK", "AU"), quota=500)
        self.assertEqual(criteria.quota, MAX_CAMPAIGN_QUOTA)
        self.assertEqual(criteria.quota, 100)

    def test_multi_engine_failover_without_rate_limit_stall(self):
        def fail_engine(q, n):
            raise RuntimeError("HTTP 429 Rate Limit")

        def succeed_engine(q, n):
            return [{"href": "https://jobs.lever.co/skyralio/1", "title": "Python", "snippet": "UK"}]

        with patch.dict(
            "lead_generation.discovery.PROVIDER_REGISTRY",
            {"duckduckgo": fail_engine, "searxng": succeed_engine},
        ):
            results = search_web(
                "unique_failover_test_query",
                providers=("duckduckgo", "searxng"),
                use_cache=False,
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["href"], "https://jobs.lever.co/skyralio/1")

    def test_rate_limited_provider_uses_retry_after_before_failover(self):
        request = httpx.Request("POST", "https://search.example.test")
        response = httpx.Response(
            429,
            request=request,
            headers={"Retry-After": "17"},
        )

        def limited_engine(q, n):
            raise httpx.HTTPStatusError("limited", request=request, response=response)

        def succeed_engine(q, n):
            return [{"href": "https://jobs.lever.co/skyralio/1", "title": "Python"}]

        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=1200.0)
        with patch.dict(
            "lead_generation.discovery.PROVIDER_REGISTRY",
            {"limited_test": limited_engine, "searxng": succeed_engine},
        ), patch.dict(
            "lead_generation.discovery.CIRCUIT_BREAKERS",
            {"limited_test": breaker, "searxng": CircuitBreaker()},
        ):
            results = search_web(
                "retry_after_failover_test_query",
                providers=("limited_test", "searxng"),
                use_cache=False,
            )

        self.assertEqual(len(results), 1)
        self.assertGreater(breaker.tripped_until, 0)
        self.assertFalse(breaker.is_available())

    def test_circuit_breaker_and_serp_cache(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=10.0)
        self.assertTrue(cb.is_available())
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.is_available())
        cb.record_success()
        self.assertTrue(cb.is_available())

        test_query = "test_unique_query_cache_check"
        mock_results = [{"href": "https://jobs.lever.co/testco/1", "title": "Test"}]
        set_cached_search(test_query, mock_results, ttl_seconds=60)
        self.assertEqual(get_cached_search(test_query), mock_results)

        page_results = [{"href": "https://example.test/page-2", "title": "Page 2"}]
        set_cached_search(test_query, page_results, ttl_seconds=60, page=2)
        self.assertEqual(get_cached_search(test_query, page=2), page_results)
        self.assertEqual(get_cached_search(test_query), mock_results)

    def test_configured_search_api_is_prioritized(self):
        calls = []

        def fake_provider(name):
            def search(query, max_results, **kwargs):
                calls.append(name)
                return [{"href": f"https://example.test/{name}", "title": name}]
            return search

        with patch.dict(
            "lead_generation.discovery.PROVIDER_REGISTRY",
            {"serper": fake_provider("serper"), "duckduckgo": fake_provider("duckduckgo")},
            clear=True,
        ), patch.dict(os.environ, {"SERPER_API_KEY": "configured"}):
            results = search_web("provider_priority_test", use_cache=False)

        self.assertEqual(calls, ["serper"])
        self.assertEqual(results[0]["title"], "serper")

    def test_yahoo_url_only_result_falls_through(self):
        calls = []

        def yahoo(query, max_results):
            calls.append("yahoo")
            return [{
                "href": "https://www.linkedin.com/in/alex-smith",
                "title": "https://www.linkedin.com/in/alex-smith",
                "snippet": "",
            }]

        def fallback(query, max_results):
            calls.append("fallback")
            return [{
                "href": "https://www.linkedin.com/in/alex-smith",
                "title": "Alex Smith - CEO - Example Co",
                "snippet": "CEO at Example Co",
            }]

        with patch.dict(
            "lead_generation.discovery.PROVIDER_REGISTRY",
            {"yahoo_html": yahoo, "fallback": fallback},
            clear=True,
        ):
            results = search_web(
                "yahoo_metadata_test",
                providers=("yahoo_html", "fallback"),
                use_cache=False,
            )

        self.assertEqual(calls, ["yahoo", "fallback"])
        self.assertEqual(results[0]["title"], "Alex Smith - CEO - Example Co")

    def test_company_discovery_uses_serper_pages(self):
        requested_pages = []

        def fake_search(query, max_results, providers=None, page=1):
            requested_pages.append((query, page, providers))
            return [
                {
                    "href": f"https://jobs.lever.co/company-{page}-{index}",
                    "title": "Software Engineer",
                    "snippet": "",
                }
                for index in range(20)
            ]

        with patch.dict(os.environ, {"SERPER_API_KEY": "configured"}), patch(
            "lead_generation.discovery.search_web", side_effect=fake_search
        ):
            discovered = discover_company_slugs(
                "Software Engineer",
                "US",
                max_results=100,
                use_curated_fallback=False,
            )

        self.assertEqual([page for _, page, _ in requested_pages], [1, 2, 3, 4, 5])
        self.assertTrue(all(providers == ("serper",) for _, _, providers in requested_pages))
        self.assertEqual(len(discovered), 100)

    def test_linkedin_company_match_accepts_common_suffixes(self):
        cases = (
            ("Fanatics Betting & Gaming", "Alex Smith - VP of Engineering - Fanatics"),
            ("Civis Analytics", "Alex Smith - CEO - Civis"),
            ("Lightyear-ai", "Alex Smith - CTO - Lightyear"),
        )
        for company, title in cases:
            with self.subTest(company=company):
                identity = parse_linkedin_snippet(
                    title=title,
                    snippet="",
                    url="https://www.linkedin.com/in/alex-smith",
                    company_name=company,
                )
                self.assertIsNotNone(identity)

    # ------------------------------------------------------------------
    # Gate 2A & 2B: Canonical Domain & RFC 5321/7505 DNS Router
    # ------------------------------------------------------------------
    def test_canonical_domain_normalization_and_ats_trap_guard(self):
        # Must reject ATS and social domains
        self.assertIsNone(normalize_canonical_domain("https://www.lever.co/about"))
        self.assertIsNone(normalize_canonical_domain("https://boards.greenhouse.io/monzo"))
        self.assertIsNone(normalize_canonical_domain("https://uk.linkedin.com/company/skyral"))
        # Must strip career subdomains and preserve multi-part TLDs (.co.uk, .com.au)
        self.assertEqual(normalize_canonical_domain("https://careers.skyral.io/jobs"), "skyral.io")
        self.assertEqual(normalize_canonical_domain("https://jobs.deliveroo.co.uk/roles"), "deliveroo.co.uk")
        self.assertEqual(normalize_canonical_domain("https://work.canva.com.au"), "canva.com.au")

    def test_extract_domain_from_ats_navbar_html(self):
        html_sample = """
        <header>
            <a href="https://jobs.lever.co">Powered by Lever</a>
            <a href="https://twitter.com/skyral">Twitter</a>
            <a href="https://www.skyral.io/about">Company Website</a>
        </header>
        """
        self.assertEqual(extract_domain_from_ats_html(html_sample, "skyralio"), "skyral.io")

    def test_search_fallback_rejects_unrelated_third_party_domain(self):
        board = type(
            "Board",
            (),
            {
                "slug": "titan-ai",
                "careers_page_url": None,
            },
        )()
        with patch(
            "lead_generation.domain_resolver.search_web",
            return_value=[
                {"href": "https://seamless.ai/company/titan-ai"},
                {"href": "https://titan.ai/about"},
            ],
        ):
            domain, _ = resolve_canonical_domain(board)

        self.assertEqual(domain, "titan.ai")

    def test_domain_hint_rejects_unrelated_third_party_domain(self):
        board = type(
            "Board",
            (),
            {
                "slug": "titan-ai",
                "careers_page_url": None,
            },
        )()
        job = ATSJob(
            platform="ashby",
            board_slug="titan-ai",
            external_id="1",
            title="Software Engineer",
            location="US",
            absolute_url="https://jobs.ashbyhq.com/titan-ai/1",
            company_domain_hint="seamless.ai",
            company_name_hint="Titan AI",
        )
        with patch("lead_generation.domain_resolver.search_web", return_value=[]):
            domain, _ = resolve_canonical_domain(board, [job])

        self.assertEqual(domain, "titan-ai.com")

    def test_split_domain_candidates_skyral_case(self):
        cands = generate_split_domain_candidates("skyralio", "skyral.com")
        self.assertIn("skyral.com", cands)
        self.assertIn("skyral.io", cands)

    def test_rfc7505_null_mx_and_provider_fingerprinting(self):
        self.assertEqual(infer_edge_provider("aspmx.l.google.com.")[0], "GOOGLE_WORKSPACE")
        self.assertEqual(infer_edge_provider("acme-com.mail.protection.outlook.com")[0], "MICROSOFT_365")
        self.assertEqual(infer_edge_provider("eu-smtp-inbound-1.mimecast.com")[0], "MIMECAST_SEG")

        # Mock RFC 7505 Null MX ('0 .') response
        with patch(
            "lead_generation.domain_resolver._doh_query",
            return_value={"Status": 0, "Answer": [{"type": 15, "data": "0 .", "TTL": 3600}]},
        ):
            res = resolve_domain_mail_routing("nullmx-example.com", use_cache=False)
            self.assertEqual(res["status"], RoutingStatus.NULL_MX.value)
            self.assertFalse(res["retryable"])

    def test_dns_ipv6_only_and_implicit_a_fallback(self):
        # Test IPv6-only MX target
        with patch(
            "lead_generation.domain_resolver._doh_query",
            return_value={"Status": 0, "Answer": [{"type": 15, "data": "10 mx6.example.com.", "TTL": 3600}]},
        ), patch(
            "lead_generation.domain_resolver.host_has_ip_address",
            return_value=(False, True, False, False),
        ):
            res = resolve_domain_mail_routing("ipv6only-example.com", use_cache=False)
            self.assertEqual(res["status"], RoutingStatus.IPV6_ONLY_UNREACHABLE.value)

        # Test RFC 5321 §5.1 Implicit A Fallback when MX is empty
        with patch(
            "lead_generation.domain_resolver._doh_query",
            return_value={"Status": 0, "Answer": []},
        ), patch(
            "lead_generation.domain_resolver.host_has_ip_address",
            return_value=(True, False, False, False),
        ):
            res = resolve_domain_mail_routing("implicit-example.com", use_cache=False)
            self.assertEqual(res["status"], RoutingStatus.NO_MX_IMPLICIT_FALLBACK.value)

    # ------------------------------------------------------------------
    # Gate 3 & 4: Executive Identity & 3-Tier Contact Waterfall
    # ------------------------------------------------------------------
    def test_linkedin_snippet_active_vs_former_executive(self):
        # Active CEO
        active = parse_linkedin_snippet(
            title="Naomi Hulme - Co-Founder & CEO - Skyral | LinkedIn",
            snippet="Co-Founder & CEO at Skyral Defence & Government.",
            url="https://uk.linkedin.com/in/naomi-hulme",
            company_name="Skyral",
        )
        self.assertIsNotNone(active)
        self.assertEqual(active.confidence, IdentityConfidence.CURRENT_CONFIRMED)
        self.assertEqual(active.normalized_name, "naomi hulme")

        # Former CEO -> must be downgraded to AMBIGUOUS_NEEDS_REVIEW
        former = parse_linkedin_snippet(
            title="John Smith - Former CEO - Skyral | LinkedIn",
            snippet="Previously Chief Executive Officer at Skyral (2020-2022).",
            url="https://uk.linkedin.com/in/john-smith",
            company_name="Skyral",
        )
        self.assertIsNotNone(former)
        self.assertEqual(former.confidence, IdentityConfidence.AMBIGUOUS_NEEDS_REVIEW)

        # Truncated snippet '...' -> must be downgraded to AMBIGUOUS_NEEDS_REVIEW
        truncated = parse_linkedin_snippet(
            title="Jane Doe - Chief Technology Officer at Skyral...",
            snippet="CTO...",
            url="https://uk.linkedin.com/in/jane-doe",
            company_name="Skyral",
        )
        self.assertIsNotNone(truncated)
        self.assertEqual(truncated.confidence, IdentityConfidence.AMBIGUOUS_NEEDS_REVIEW)

    def test_name_normalization_and_hyphenated_permutations(self):
        self.assertEqual(normalize_person_name("Dr. René François, PhD, MBA"), "Rene Francois")
        perms = generate_executive_permutations("Naomi Hulme-Smith", "skyral.io")
        self.assertEqual(len(perms), 4)
        self.assertEqual(perms[0], "naomi.hulmesmith@skyral.io")
        self.assertIn("naomi.hulme@skyral.io", perms)

    def test_role_email_guard_and_format_inference(self):
        self.assertTrue(is_role_or_generic_email("privacy@skyral.io"))
        self.assertTrue(is_role_or_generic_email("careers@skyral.io"))
        self.assertFalse(is_role_or_generic_email("naomi.hulme@skyral.io"))

        pattern = infer_email_pattern_from_samples(
            ["privacy@skyral.io", "alex.jones@skyral.io"], "skyral.io"
        )
        self.assertEqual(pattern, "first.last")

    # ------------------------------------------------------------------
    # Gate 5: Canary & SEG Protection + 3-Axis Epistemic Lead Grader
    # ------------------------------------------------------------------
    def test_3_axis_epistemic_lead_grading(self):
        # Grade A: Direct published match + MX_RESOLVED
        self.assertEqual(
            compute_lead_grade(
                RoutingStatus.MX_RESOLVED,
                IdentityConfidence.CURRENT_CONFIRMED,
                AttributionStatus.PUBLISHED_DIRECT_MATCH,
                SmtpStatus.PROBE_BLOCKED_PORT25,
            ),
            LeadGrade.GRADE_A,
        )
        # Grade B: Inferred permutation + SMTP_RCPT_ACCEPTED (negative canary)
        self.assertEqual(
            compute_lead_grade(
                RoutingStatus.MX_RESOLVED,
                IdentityConfidence.CURRENT_CONFIRMED,
                AttributionStatus.INFERRED_CANDIDATE,
                SmtpStatus.SMTP_RCPT_ACCEPTED,
            ),
            LeadGrade.GRADE_B,
        )
        # Grade C: Inferred permutation + Catch-All / Port 25 Blocked / SEG
        self.assertEqual(
            compute_lead_grade(
                RoutingStatus.MX_RESOLVED,
                IdentityConfidence.CURRENT_CONFIRMED,
                AttributionStatus.INFERRED_CANDIDATE,
                SmtpStatus.CATCH_ALL_SUSPECTED,
            ),
            LeadGrade.GRADE_C,
        )
        # Review Queue: Ambiguous identity or implicit fallback DNS
        self.assertEqual(
            compute_lead_grade(
                RoutingStatus.NO_MX_IMPLICIT_FALLBACK,
                IdentityConfidence.CURRENT_CONFIRMED,
                AttributionStatus.INFERRED_CANDIDATE,
                SmtpStatus.SMTP_RCPT_ACCEPTED,
            ),
            LeadGrade.REVIEW_QUEUE,
        )

    def test_seg_provider_skips_smtp_probe(self):
        comp = CompanyRecord(
            canonical_domain="acme.com",
            canonical_name="Acme",
            routing_status=RoutingStatus.MX_RESOLVED,
            primary_mx_host="eu-smtp-inbound-1.mimecast.com",
            inferred_provider="MIMECAST_SEG",
        )
        obs = [
            ContactObservation(
                normalized_email="jane.doe@acme.com",
                discovery_tier="TIER_3_PERMUTATION",
                attribution_status=AttributionStatus.INFERRED_CANDIDATE,
            )
        ]
        probed = probe_company_candidates(comp, obs, enable_live_smtp=True)
        self.assertEqual(probed[0].smtp_status, SmtpStatus.PROBE_SKIPPED_SEG)

    # ------------------------------------------------------------------
    # Gate 6 & Full 7-Gate End-to-End Pipeline Execution
    # ------------------------------------------------------------------
    def test_end_to_end_7_gate_campaign_and_transactional_outbox(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name

        try:
            outbox = TransactionalOutbox(db_path=db_path)
            pipeline = LeadGenerationPipeline(outbox=outbox)
            criteria = CampaignCriteria(role="Python Developer", locations=("UK",), quota=5)

            jobs = [
                ATSJob(
                    platform="lever",
                    board_slug="skyralio",
                    external_id="101",
                    title="Senior Python Developer",
                    location="London, UK",
                    absolute_url="https://jobs.lever.co/skyralio/101",
                    company_size="50-100",
                    company_domain_hint="skyral.io",
                    company_name_hint="Skyral",
                )
            ]

            with patch("lead_generation.pipeline.fetch_board_jobs", return_value=jobs), patch(
                "lead_generation.domain_resolver.resolve_domain_mail_routing",
                return_value={
                    "status": RoutingStatus.MX_RESOLVED.value,
                    "primary_host": "aspmx.l.google.com",
                    "all_hosts": ["aspmx.l.google.com"],
                    "inferred_provider": "GOOGLE_WORKSPACE",
                    "provider_evidence": "aspmx.l.google.com",
                    "ttl": 3600,
                    "retryable": False,
                },
            ), patch(
                "lead_generation.identity.search_web",
                return_value=[
                    {
                        "href": "https://uk.linkedin.com/in/naomi-hulme",
                        "title": "Naomi Hulme - Co-Founder & CEO - Skyral | LinkedIn",
                        "snippet": "Co-Founder & CEO at Skyral Defence & Government.",
                    }
                ],
            ):
                result = pipeline.run_campaign(
                    criteria,
                    ats_urls=["https://jobs.lever.co/skyralio/101"],
                    enable_web_mentions=False,
                    enable_live_smtp=False,
                )

            self.assertEqual(len(result["leads"]), 1)
            lead = result["leads"][0]
            self.assertEqual(lead.company.canonical_domain, "skyral.io")
            self.assertEqual(lead.exec_full_name, "Naomi Hulme")
            self.assertEqual(lead.best_email, "naomi.hulme@skyral.io")
            self.assertEqual(lead.lead_grade, LeadGrade.GRADE_C)

            # Verify idempotent outbox claiming and export state transitions
            batch_id, claimed = outbox.claim_pending_batch(batch_limit=10)
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["best_email"], "naomi.hulme@skyral.io")

            # Second concurrent claim must return 0 rows (preventing double export)
            _, second_claim = outbox.claim_pending_batch(batch_limit=10)
            self.assertEqual(len(second_claim), 0)

            exported_count = outbox.mark_batch_exported(batch_id)
            self.assertEqual(exported_count, 1)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    # ------------------------------------------------------------------
    # Phone Extraction ("Sweet Spot") & LinkedIn Must-Have Tests
    # ------------------------------------------------------------------
    def test_phone_extractor_formats_and_normalization(self):
        from lead_generation.phone_extractor import (
            extract_phones_from_text,
            normalize_phone_number,
        )

        # UK Numbers
        uk_norm = normalize_phone_number("020 3808 9700", default_country="UK")
        self.assertEqual(uk_norm, "+44 20 3808 9700")
        # UK Ofcom drama range (020 7946 0xxx) must be rejected
        self.assertIsNone(normalize_phone_number("020 7946 0958", default_country="UK"))

        uk_tollfree = normalize_phone_number("+44 (0)800 802 1281", default_country="UK")
        self.assertEqual(uk_tollfree, "+44 800 802 1281")

        # AU Numbers
        au_sydney = normalize_phone_number("(02) 8582 0100", default_country="AU")
        self.assertEqual(au_sydney, "+61 2 8582 0100")

        au_tollfree = normalize_phone_number("1300 000 777", default_country="AU")
        self.assertEqual(au_tollfree, "+61 1300 000 777")

        # US / International E.164
        us_num = normalize_phone_number("+1 (888) 963-8010", default_country="US")
        self.assertEqual(us_num, "+1 888-963-8010")

        # Noise rejection (dates, timestamps, IP addresses)
        self.assertIsNone(normalize_phone_number("2024-05-12"))
        self.assertIsNone(normalize_phone_number("192.168.1.1"))
        self.assertIsNone(normalize_phone_number("443"))

        # Unstructured text extraction
        sample_text = (
            "Contact our London office at +44 20 3808 9700 or call direct line +44 20 7846 0192. "
            "For Australia enquiries, dial +61 2 8582 0100."
        )
        extracted = extract_phones_from_text(sample_text)
        extracted_nums = [p[0] for p in extracted]
        self.assertIn("+44 20 3808 9700", extracted_nums)
        self.assertIn("+44 20 7846 0192", extracted_nums)
        self.assertIn("+61 2 8582 0100", extracted_nums)

    def test_strict_linkedin_employer_clause_rejects_third_party_ceo(self):
        # False positive case: CEO of another company who mentions PostHog in their bio
        false_positive = parse_linkedin_snippet(
            title="Matt Ghering - CEO - OtherStartup | LinkedIn",
            snippet="Building SaaS tools. We use PostHog for product analytics and feature flags.",
            url="https://www.linkedin.com/in/matt-ghering",
            company_name="PostHog",
        )
        self.assertIsNone(false_positive)

        # True positive case: Company is explicitly in the LinkedIn title/employer clause
        true_positive = parse_linkedin_snippet(
            title="James Hawkins - Co-Founder & CEO - PostHog | LinkedIn",
            snippet="Co-Founder & CEO at PostHog.",
            url="https://uk.linkedin.com/in/j-hawkins",
            company_name="PostHog",
        )
        self.assertIsNotNone(true_positive)
        self.assertEqual(true_positive.full_name, "James Hawkins")
        self.assertEqual(true_positive.confidence, IdentityConfidence.CURRENT_CONFIRMED)

    def test_no_fake_phone_generated_when_none_published(self):
        from lead_generation.phone_extractor import resolve_company_phone

        comp = CompanyRecord(canonical_domain="unknown-startup.io", canonical_name="Unknown Startup")
        phone, ptype = resolve_company_phone(
            comp,
            job_payloads=[{"description": "We are hiring a remote engineer. Apply online."}],
            default_country="UK",
            enable_dom_scrape=False,
        )
        self.assertIsNone(phone)
        self.assertEqual(ptype, "NONE")

    def test_pipeline_enforces_linkedin_must_have_and_phone(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
            db_path = tmp_db.name
        try:
            outbox = TransactionalOutbox(db_path=db_path)
            pipeline = LeadGenerationPipeline(outbox=outbox)
            criteria = CampaignCriteria(role="Engineer", locations=("UK",), quota=5)

            mock_jobs = [
                ATSJob(
                    platform="lever",
                    board_slug="skyralio",
                    external_id="101",
                    title="Platform Engineer",
                    location="London, UK",
                    absolute_url="https://jobs.lever.co/skyralio/101",
                    raw={"description": 'Questions? Call <a href="tel:+442038089700">+44 20 3808 9700</a>'},
                ),
            ]

            with patch("lead_generation.pipeline.fetch_board_jobs", return_value=mock_jobs), patch(
                "lead_generation.domain_resolver.resolve_domain_mail_routing",
                return_value={
                    "status": RoutingStatus.MX_RESOLVED.value,
                    "primary_host": "aspmx.l.google.com",
                    "all_hosts": ["aspmx.l.google.com"],
                    "inferred_provider": "GOOGLE_WORKSPACE",
                    "provider_evidence": "aspmx.l.google.com",
                    "ttl": 3600,
                    "retryable": False,
                },
            ), patch(
                "lead_generation.identity.search_web",
                return_value=[
                    {
                        "href": "https://uk.linkedin.com/in/naomi-hulme",
                        "title": "Naomi Hulme - Co-Founder & CEO - Skyral | LinkedIn",
                        "snippet": "Co-Founder & CEO at Skyral.",
                    }
                ],
            ):
                result = pipeline.run_campaign(
                    criteria,
                    ats_urls=["https://jobs.lever.co/skyralio/101"],
                    enable_web_mentions=False,
                    enable_live_smtp=False,
                    require_linkedin=True,
                )

            self.assertEqual(len(result["leads"]), 1)
            lead = result["leads"][0]
            # Verify LinkedIn Must Have
            self.assertTrue(lead.linkedin_url.startswith("https://"))
            self.assertIn("linkedin.com/in/", lead.linkedin_url)
            self.assertNotEqual(lead.exec_full_name, "Unresolved Executive")

            # Verify Genuine Extracted Phone
            self.assertEqual(lead.phone_number, "+44 20 3808 9700")
            self.assertEqual(lead.phone_type, "PUBLISHED_TEL_LINK")
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_python_dev_10_to_50_company_size_and_fictive_phone_rejection(self):
        from lead_generation.phone_extractor import normalize_phone_number

        # Google libphonenumber rejects US 555-0199 fictive range
        self.assertIsNone(normalize_phone_number("+1 415 555 0199", default_country="US"))

        jobs = [
            ATSJob(
                platform="ashby",
                board_slug="smallstartup",
                external_id="1",
                title="Senior Python Engineer",
                location="New York, US",
                absolute_url="https://jobs.ashbyhq.com/smallstartup/1",
                company_size="10-50",
            ),
            ATSJob(
                platform="lever",
                board_slug="ukstartup",
                external_id="2",
                title="Backend Engineer",
                location="London, UK",
                absolute_url="https://jobs.lever.co/ukstartup/2",
                company_size="25 employees",
                raw={"descriptionPlain": "Building distributed systems in Python and FastAPI."},
            ),
            ATSJob(
                platform="greenhouse",
                board_slug="tinyco",
                external_id="3",
                title="Python Developer",
                location="London, UK",
                absolute_url="https://boards.greenhouse.io/tinyco/jobs/3",
                company_size="2-5 employees",
            ),
            ATSJob(
                platform="greenhouse",
                board_slug="bigcorp",
                external_id="4",
                title="Python Developer",
                location="San Francisco, US",
                absolute_url="https://boards.greenhouse.io/bigcorp/jobs/4",
                company_size="51-200 employees",
            ),
        ]

        uk_matches = filter_jobs(jobs, "python dev", "UK", min_company_size=10, max_company_size=50)
        us_matches = filter_jobs(jobs, "python dev", "US", min_company_size=10, max_company_size=50)

        self.assertEqual([j.board_slug for j in uk_matches], ["ukstartup"])
        self.assertEqual([j.board_slug for j in us_matches], ["smallstartup"])

    def test_global_countries_and_one_unique_company_per_row(self):
        from lead_generation.phone_extractor import normalize_phone_number

        # UAE / Dubai phone normalization (+971)
        uae_phone = normalize_phone_number("+971 4 313 2000", default_country="Dubai")
        self.assertEqual(uae_phone, "+971 4 313 2000")

        # Germany / Berlin phone normalization (+49)
        de_phone = normalize_phone_number("+49 30 2555 980", default_country="Germany")
        self.assertEqual(de_phone, "+49 30 2555980")

        # Intern roles excluded while remote/UAE/EU/Canada roles match
        jobs = [
            ATSJob(
                platform="ashby",
                board_slug="ziina",
                external_id="1",
                title="Senior Backend Engineer (Python)",
                location="Dubai, UAE",
                absolute_url="https://jobs.ashbyhq.com/ziina/1",
                company_size="10-50",
            ),
            ATSJob(
                platform="ashby",
                board_slug="ziina",
                external_id="2",
                title="Full Stack Engineer (Python)",
                location="Dubai, UAE",
                absolute_url="https://jobs.ashbyhq.com/ziina/2",
                company_size="10-50",
            ),
            ATSJob(
                platform="ashby",
                board_slug="ziina",
                external_id="3",
                title="Software Engineering Intern (Python)",
                location="Dubai, UAE",
                absolute_url="https://jobs.ashbyhq.com/ziina/3",
                company_size="10-50",
            ),
            ATSJob(
                platform="ashby",
                board_slug="langfuse",
                external_id="4",
                title="Software Engineer (Python)",
                location="Berlin, Germany (Remote EU)",
                absolute_url="https://jobs.ashbyhq.com/langfuse/4",
                company_size="10-50",
            ),
        ]

        dubai_matches = filter_jobs(jobs, "python developer", "Dubai", min_company_size=10, max_company_size=50)
        # Intern role #3 must be filtered out; #1 and #2 match Dubai + Langfuse matches via Remote
        self.assertEqual(len([j for j in dubai_matches if j.board_slug == "ziina"]), 2)
        self.assertTrue(all("Intern" not in j.title for j in dubai_matches))

        # Verify pipeline produces strictly 1 row per unique company even when quota=10 and company has multiple jobs
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
            db_path = tmp_db.name
        try:
            outbox = TransactionalOutbox(db_path=db_path)
            pipeline = LeadGenerationPipeline(outbox=outbox)
            criteria = CampaignCriteria(role="Python Developer", locations=("Dubai",), quota=10)

            with patch("lead_generation.pipeline.fetch_board_jobs", return_value=jobs[:2]), patch(
                "lead_generation.domain_resolver.resolve_domain_mail_routing",
                return_value={
                    "status": RoutingStatus.MX_RESOLVED.value,
                    "primary_host": "aspmx.l.google.com",
                    "all_hosts": ["aspmx.l.google.com"],
                    "inferred_provider": "GOOGLE_WORKSPACE",
                    "provider_evidence": "aspmx.l.google.com",
                    "ttl": 3600,
                    "retryable": False,
                },
            ), patch(
                "lead_generation.identity.search_web",
                return_value=[
                    {
                        "href": "https://ae.linkedin.com/in/faisal-toukan",
                        "title": "Faisal Toukan - Co-Founder & CEO - Ziina | LinkedIn",
                        "snippet": "Co-Founder & CEO at Ziina.",
                    }
                ],
            ):
                res = pipeline.run_campaign(
                    criteria,
                    ats_urls=["https://jobs.ashbyhq.com/ziina/1"],
                    enable_web_mentions=False,
                    enable_live_smtp=False,
                    require_linkedin=True,
                )

            # Even though Ziina had 2 open Python roles and quota=10, only 1 unique row is emitted for Ziina
            self.assertEqual(len(res["leads"]), 1)
            self.assertIn("(+1 more open roles)", res["leads"][0].job_title_signal)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_custom_startup_size_parsing_and_downloadable_csv_when_sheets_disconnected(self):
        import django
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()
        from lead_generation.filtering import parse_company_size_bounds
        from agent.tools.lead_generation_tool import execute_lead_generation_and_export
        from agent.cards.builders import build_sheet_view

        self.assertEqual(parse_company_size_bounds("10-50"), (10, 50))
        self.assertEqual(parse_company_size_bounds("under 25"), (1, 25))
        self.assertEqual(parse_company_size_bounds("up to 30 employees"), (1, 30))
        self.assertEqual(parse_company_size_bounds("< 20"), (1, 20))
        self.assertEqual(parse_company_size_bounds("50-200"), (50, 200))
        self.assertEqual(parse_company_size_bounds("50+"), (50, 500))
        self.assertEqual(parse_company_size_bounds("seed stage"), (1, 25))
        self.assertEqual(parse_company_size_bounds("Series A"), (15, 100))

        # Execute lead generation with user=None (simulating Google Sheets not connected)
        with patch.object(
            LeadGenerationPipeline,
            "run_campaign",
            return_value={
                "leads": [],
                "boards": [],
                "jobs": [],
                "dropped_companies": [],
                "regional_quotas": {"US": 2, "UK": 2},
            },
        ):
            telemetry = execute_lead_generation_and_export(
                user=None,
                role="Python Developer",
                locations="US, UK",
                company_size="under 25",
                quota=4,
                enable_dom_scrape=False,
                enable_live_smtp=False,
                emit_langchain_events=False,
            )
        self.assertEqual(telemetry["campaign_parameters"]["company_size_filter"], "1-25 employees")
        self.assertEqual(telemetry["download_csv_url"], "/api/leads/download/csv/")
        self.assertEqual(telemetry["download_jsonl_url"], "/api/leads/download/jsonl/")
        self.assertFalse(telemetry["synced_to_google_sheets"])
        self.assertTrue(os.path.exists(telemetry["pure_python_export"]["csv_file_path"]))

        card_data = build_sheet_view({**telemetry, "rows": [["PostHog", "posthog.com", "James Hawkins", "CEO"]]})
        self.assertIsNotNone(card_data)
        self.assertEqual(card_data["download_csv_url"], "/api/leads/download/csv/")
        self.assertEqual(card_data["synced_to_google_sheets"], False)


if __name__ == "__main__":
    unittest.main()

