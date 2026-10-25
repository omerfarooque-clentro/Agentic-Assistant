from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import time
from urllib.parse import urlparse

import httpx

from .discovery import search_web
from .models import ATSBoard, ATSJob, CompanyRecord, RoutingStatus

# ---------------------------------------------------------------------------
# Gate 2A: Canonical Corporate Domain Resolution & Edge-Case Guards
# ---------------------------------------------------------------------------

DISALLOWED_DOMAINS = {
    "lever.co",
    "jobs.lever.co",
    "greenhouse.io",
    "greenhouse.com",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "ashbyprd.com",
    "ashby.io",
    "workable.com",
    "apply.workable.com",
    "breezy.hr",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "github.com",
    "glassdoor.com",
    "glassdoor.co.uk",
    "indeed.com",
    "ycombinator.com",
    "crunchbase.com",
    "wellfound.com",
    "angel.co",
    "google.com",
    "duckduckgo.com",
    "bing.com",
    "yahoo.com",
    "brave.com",
    "wikimedia.org",
    "wikipedia.org",
}

ATS_PROVIDER_KEYWORDS = (
    "greenhouse", "ashby", "lever", "workable", "breezy",
    "smartrecruiters", "recruitee", "personio", "bamboohr", "workday",
)

MULTI_PART_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au",
    "co.nz", "co.jp", "co.in", "com.br", "com.sg",
    "co.ae", "ae.org", "com.mx", "co.za", "co.il",
}

SUBDOMAIN_PREFIXES_TO_STRIP = {
    "www", "careers", "jobs", "work", "join", "talent", "apply", "boards", "hr", "people",
}

# Curated slug -> canonical domain map for fast offline/deterministic resolution
KNOWN_SLUG_DOMAINS: dict[str, tuple[str, str]] = {
    "skyralio": ("skyral.io", "Skyral"),
    "posthog": ("posthog.com", "PostHog"),
    "synthesia": ("synthesia.io", "Synthesia"),
    "cleo": ("web.meetcleo.com", "Cleo"),
    "primer": ("primer.io", "Primer"),
    "paddle": ("paddle.com", "Paddle"),
    "tractable": ("tractable.ai", "Tractable"),
    "signalai": ("signal-ai.com", "Signal AI"),
    "tessian": ("tessian.com", "Tessian"),
    "huma": ("huma.com", "Huma"),
    "complyadvantage": ("complyadvantage.com", "ComplyAdvantage"),
    "faculty": ("faculty.ai", "Faculty"),
    "incidentio": ("incident.io", "incident.io"),
    "Griffin": ("griffin.com", "Griffin"),
    "griffin": ("griffin.com", "Griffin"),
    "speakeasy": ("speakeasy.com", "Speakeasy"),
    "safetyculture": ("safetyculture.com", "SafetyCulture"),
    "cultureamp": ("cultureamp.com", "Culture Amp"),
    "immutable": ("immutable.com", "Immutable"),
    "eucalyptus": ("eucalyptus.vc", "Eucalyptus"),
    "morsemicro": ("morsemicro.com", "Morse Micro"),
    "forage": ("theforage.com", "Forage"),
    "propic": ("propic.com.au", "Propic"),
    "endaoment": ("endaoment.org", "Endaoment"),
    "linear": ("linear.app", "Linear"),
    "vercel": ("vercel.com", "Vercel"),
    "mistral": ("mistral.ai", "Mistral AI"),
    "elevenlabs": ("elevenlabs.io", "ElevenLabs"),
    "tailscale": ("tailscale.com", "Tailscale"),
    "skyspecs": ("skyspecs.com", "SkySpecs"),
    "augmodo": ("augmodo.com", "Augmodo"),
    "supabase": ("supabase.com", "Supabase"),
    "resend": ("resend.com", "Resend"),
    "neon": ("neon.tech", "Neon"),
    "planetscale": ("planetscale.com", "PlanetScale"),
    "temporal": ("temporal.io", "Temporal"),
    "pulumi": ("pulumi.com", "Pulumi"),
    "pinecone": ("pinecone.io", "Pinecone"),
    "cursor": ("cursor.com", "Cursor"),
    "langfuse": ("langfuse.com", "Langfuse"),
    "Qdrant": ("qdrant.tech", "Qdrant"),
    "qdrant": ("qdrant.tech", "Qdrant"),
    "weaviate": ("weaviate.io", "Weaviate"),
    "railway": ("railway.app", "Railway"),
    "render": ("render.com", "Render"),
    "flyio": ("fly.io", "Fly.io"),
    "modal": ("modal.com", "Modal"),
    "baseten": ("baseten.co", "Baseten"),
    "replicate": ("replicate.com", "Replicate"),
    "inngest": ("inngest.com", "Inngest"),
    "triggerdev": ("trigger.dev", "Trigger.dev"),
    "calcom": ("cal.com", "Cal.com"),
    "dub": ("dub.co", "Dub"),
    "tinybird": ("tinybird.co", "Tinybird"),
    "turso": ("turso.tech", "Turso"),
    "convex": ("convex.dev", "Convex"),
    "Mintlify": ("mintlify.com", "Mintlify"),
    "mintlify": ("mintlify.com", "Mintlify"),
    "unstructured": ("unstructured.io", "Unstructured"),
    "llama-index": ("llamaindex.ai", "LlamaIndex"),
    "llamaindex": ("llamaindex.ai", "LlamaIndex"),
    "pydantic": ("pydantic.dev", "Pydantic"),
    "astral": ("astral.sh", "Astral"),
    "careem": ("careem.com", "Careem"),
    "tabby": ("tabby.ai", "Tabby"),
    "tamara": ("tamara.co", "Tamara"),
    "huspy": ("huspy.com", "Huspy"),
    "sarwa": ("sarwa.co", "Sarwa"),
    "ziina": ("ziina.com", "Ziina"),
    "baraka": ("getbaraka.com", "Baraka"),
    "lean": ("leantech.me", "Lean Technologies"),
    "leantech": ("leantech.me", "Lean Technologies"),
    "nybl": ("nybl.com", "nybl"),
    "ai71": ("ai71.ai", "AI71"),
}


def normalize_canonical_domain(url_or_domain: str) -> str | None:
    """
    Extracts and normalizes the root corporate domain from a URL or hostname.
    Filters out ATS domains, social networks, and strips career subdomains.
    """
    if not url_or_domain:
        return None
    raw = url_or_domain.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return None

    # Check disallowed domains and ATS provider keywords
    for bad in DISALLOWED_DOMAINS:
        if host == bad or host.endswith(f".{bad}"):
            return None
    for kw in ATS_PROVIDER_KEYWORDS:
        if kw in host:
            return None

    parts = host.split(".")
    while len(parts) > 2 and parts[0] in SUBDOMAIN_PREFIXES_TO_STRIP:
        parts.pop(0)

    # Collapse to registrable root domain (respecting .co.uk, .com.au, etc.)
    if len(parts) >= 3:
        last_two = f"{parts[-2]}.{parts[-1]}"
        if last_two in MULTI_PART_TLDS:
            root = ".".join(parts[-3:])
        else:
            root = ".".join(parts[-2:])
    else:
        root = ".".join(parts)

    if any(root == bad or root.endswith(f".{bad}") for bad in DISALLOWED_DOMAINS):
        return None
    if any(kw in root for kw in ATS_PROVIDER_KEYWORDS):
        return None
    return root


def extract_domain_from_ats_html(html_text: str, slug: str) -> str | None:
    """Extracts external corporate website link from ATS board HTML header/footer."""
    if not html_text:
        return None
    hrefs = re.findall(r'href=["\'](https?://[^"\'>\s]+)["\']', html_text, re.IGNORECASE)
    slug_clean = re.sub(r"[^a-z0-9]", "", slug.lower())

    for href in hrefs:
        domain = normalize_canonical_domain(href)
        if not domain:
            continue
        # Prioritize domains whose base label overlaps with the ATS slug
        base_label = domain.split(".")[0].replace("-", "")
        if base_label in slug_clean or slug_clean in base_label:
            return domain

    return None


def generate_split_domain_candidates(slug: str, primary_domain: str | None = None) -> list[str]:
    """
    Handles the Skyral split-domain edge case (e.g., skyral.com vs skyral.io).
    Generates candidate corporate domains ordered by likelihood.
    """
    candidates: list[str] = []
    if primary_domain:
        candidates.append(primary_domain)
        base = primary_domain.split(".")[0]
    else:
        # Clean common suffixes like 'io', 'hq', 'app', 'jobs' from ATS slug
        base = slug.lower()
        if base.endswith("io") and len(base) > 4:
            candidates.append(f"{base[:-2]}.io")
        elif base.endswith("hq") and len(base) > 4:
            base = base[:-2]

    for tld in ("com", "io", "ai", "co", "co.uk", "com.au", "app"):
        cand = f"{base}.{tld}"
        if cand not in candidates:
            candidates.append(cand)
    return candidates


def resolve_canonical_domain(
    board: ATSBoard,
    jobs: list[ATSJob] | None = None,
) -> tuple[str, str]:
    """
    Gate 2A: Resolves (canonical_domain, company_name) from:
    1. ATS Job payload hints
    2. Known curated map
    3. Live ATS board HTML navbar
    4. Free web search fallback
    5. Deterministic slug heuristic
    """
    slug = board.slug.lower()
    company_name = slug.capitalize()

    if jobs:
        for job in jobs:
            if job.company_name_hint and job.company_name_hint.lower() != slug:
                company_name = job.company_name_hint
            if job.company_domain_hint:
                norm = normalize_canonical_domain(job.company_domain_hint)
                if norm:
                    return norm, company_name

    if slug in KNOWN_SLUG_DOMAINS:
        return KNOWN_SLUG_DOMAINS[slug]

    # Try scraping ATS careers HTML header
    if board.careers_page_url:
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True, max_redirects=5) as client:
                resp = client.get(board.careers_page_url)
                if resp.status_code == 200:
                    dom = extract_domain_from_ats_html(resp.text, slug)
                    if dom:
                        return dom, company_name
        except Exception:
            pass

    # Fallback: Free search engine lookup
    search_results = search_web(f'"{slug}" official website', max_results=5)
    for item in search_results:
        dom = normalize_canonical_domain(item.get("href", ""))
        if dom:
            return dom, company_name

    # Deterministic slug TLD derivation
    candidates = generate_split_domain_candidates(slug)
    return candidates[0], company_name


# ---------------------------------------------------------------------------
# Gate 2B: Audited RFC 5321 / 7505 DNS Routing Engine (DoH + dnspython + Cache)
# ---------------------------------------------------------------------------

DNS_CACHE_DB = os.path.join(os.path.dirname(__file__), ".dns_cache.db")


def _init_dns_cache() -> None:
    conn = sqlite3.connect(DNS_CACHE_DB)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dns_cache (
                domain TEXT PRIMARY KEY,
                payload_json TEXT,
                expires_at REAL
            )
        """)
        conn.commit()
    finally:
        conn.close()


_init_dns_cache()

INFERRED_PROVIDER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:^|\.)(?:aspmx\.l\.google\.com|google\.com|googlemail\.com)$", re.I), "GOOGLE_WORKSPACE"),
    (re.compile(r"\.mail\.protection\.outlook\.com$", re.I), "MICROSOFT_365"),
    (re.compile(r"\.(?:mimecast|mimecast-offshore)\.com$", re.I), "MIMECAST_SEG"),
    (re.compile(r"\.(?:pphosted|ppe-hosted)\.com$", re.I), "PROOFPOINT_SEG"),
    (re.compile(r"\.barracudanetworks\.com$", re.I), "BARRACUDA_SEG"),
    (re.compile(r"^inbound-smtp\.[a-z0-9-]+\.amazonaws\.com$", re.I), "AMAZON_SES_INBOUND"),
    (re.compile(r"\.(?:protonmail\.ch|proton\.me)$", re.I), "PROTONMAIL"),
    (re.compile(r"\.mx\.cloudflare\.net$", re.I), "CLOUDFLARE_EMAIL"),
]


def infer_edge_provider(mx_host: str) -> tuple[str, str]:
    h = mx_host.lower().rstrip(".")
    for pattern, label in INFERRED_PROVIDER_PATTERNS:
        if pattern.search(h):
            return label, h
    return "UNCLASSIFIED", h


def _doh_query(name: str, qtype: str) -> dict:
    """
    RFC 8484 JSON DNS-over-HTTPS (DoH) resolver querying Cloudflare (1.1.1.1) and Google (8.8.8.8).
    Works even when UDP Port 53 is restricted or dnspython is not installed.
    """
    endpoints = (
        ("https://cloudflare-dns.com/dns-query", {"name": name, "type": qtype}),
        ("https://dns.google/resolve", {"name": name, "type": qtype}),
    )
    headers = {"Accept": "application/dns-json"}
    last_exc: Exception | None = None

    for url, params in endpoints:
        try:
            with httpx.Client(timeout=4.0) as client:
                resp = client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            last_exc = exc
            continue

    raise TimeoutError(f"DoH resolution failed for {name} ({qtype}): {last_exc}")


def host_has_ip_address(hostname: str) -> tuple[bool, bool, bool, bool]:
    """
    Checks A (IPv4), AAAA (IPv6), and CNAME records for an MX target or fallback domain.
    Returns: (has_ipv4, has_ipv6, is_cname, had_transient_error)
    """
    clean = hostname.strip().lower().rstrip(".")
    has_v4 = False
    has_v6 = False
    is_cname = False
    transient = False

    for qtype in ("A", "AAAA"):
        try:
            data = _doh_query(clean, qtype)
            status = data.get("Status", 0)
            if status == 2:  # SERVFAIL
                transient = True
                continue
            for ans in data.get("Answer", []):
                rtype = ans.get("type")
                if rtype == 5:  # CNAME
                    is_cname = True
                elif rtype == 1:  # A (IPv4)
                    has_v4 = True
                elif rtype == 28:  # AAAA (IPv6)
                    has_v6 = True
        except Exception:
            # Fallback to OS socket resolver if DoH is unreachable
            try:
                infos = socket.getaddrinfo(clean, None)
                for info in infos:
                    if info[0] == socket.AF_INET:
                        has_v4 = True
                    elif info[0] == socket.AF_INET6:
                        has_v6 = True
            except socket.gaierror:
                pass
            except Exception:
                transient = True

    return has_v4, has_v6, is_cname, transient


def resolve_domain_mail_routing(
    domain: str,
    allow_implicit_fallback: bool = True,
    use_cache: bool = True,
) -> dict:
    """
    Audited Gate 2B DNS Routing Engine:
    - Traps RFC 7505 Null MX ('0 .') -> NULL_MX
    - Verifies MX target host A/AAAA addressability
    - Detects IPv6-only targets (IPV6_ONLY_UNREACHABLE) & CNAME targets (MX_TARGET_IS_CNAME)
    - Evaluates RFC 5321 §5.1 implicit A/AAAA fallback
    - Bounds TTL between 3600s and 86400s
    """
    clean = domain.strip().lower().rstrip(".")
    if not clean or "." not in clean:
        return {
            "status": RoutingStatus.NXDOMAIN.value,
            "primary_host": None,
            "all_hosts": [],
            "inferred_provider": "UNCLASSIFIED",
            "provider_evidence": None,
            "ttl": 3600,
            "retryable": False,
        }

    if use_cache:
        conn = sqlite3.connect(DNS_CACHE_DB)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT payload_json FROM dns_cache WHERE domain = ? AND expires_at > ?",
                (clean, time.time()),
            )
            row = cur.fetchone()
            if row:
                return json.loads(row[0])
        finally:
            conn.close()

    try:
        mx_data = _doh_query(clean, "MX")
        rcode = mx_data.get("Status", 0)

        if rcode == 3:  # NXDOMAIN
            result = {
                "status": RoutingStatus.NXDOMAIN.value,
                "primary_host": None,
                "all_hosts": [],
                "inferred_provider": "UNCLASSIFIED",
                "provider_evidence": None,
                "ttl": 3600,
                "retryable": False,
            }
            return _cache_and_return(clean, result, use_cache)

        if rcode == 2:  # SERVFAIL
            return {
                "status": RoutingStatus.DNS_TEMPORARY_FAILURE.value,
                "primary_host": None,
                "all_hosts": [],
                "inferred_provider": "UNCLASSIFIED",
                "provider_evidence": None,
                "ttl": 300,
                "retryable": True,
            }

        answers = [a for a in mx_data.get("Answer", []) if a.get("type") == 15]
        parsed_mx: list[tuple[int, str, int]] = []
        for ans in answers:
            raw_rdata = str(ans.get("data", "")).strip()
            parts = raw_rdata.split()
            if len(parts) == 2:
                try:
                    pref = int(parts[0])
                except ValueError:
                    pref = 10
                exchange = parts[1].strip()
                ttl = int(ans.get("TTL", 3600))
                parsed_mx.append((pref, exchange, ttl))

        parsed_mx.sort(key=lambda x: x[0])

        # Check RFC 7505 Null MX ('0 .')
        if parsed_mx and all(ex == "." for _, ex, _ in parsed_mx):
            result = {
                "status": RoutingStatus.NULL_MX.value,
                "primary_host": None,
                "all_hosts": [],
                "inferred_provider": "UNCLASSIFIED",
                "provider_evidence": "RFC7505_NULL_MX",
                "ttl": 86400,
                "retryable": False,
            }
            return _cache_and_return(clean, result, use_cache)

        mx_hosts = [ex.rstrip(".").lower() for _, ex, _ in parsed_mx if ex != "."]
        if mx_hosts:
            resolvable_v4: list[str] = []
            ipv6_only: list[str] = []
            cname_detected = False
            had_transient = False

            for h in mx_hosts:
                has_v4, has_v6, is_cname, trans = host_has_ip_address(h)
                if is_cname:
                    cname_detected = True
                if has_v4:
                    resolvable_v4.append(h)
                elif has_v6:
                    ipv6_only.append(h)
                elif trans:
                    had_transient = True

            raw_ttl = parsed_mx[0][2] if parsed_mx else 3600
            bounded_ttl = min(max(raw_ttl, 3600), 86400)

            if resolvable_v4:
                primary = resolvable_v4[0]
                prov, ev = infer_edge_provider(primary)
                status = (
                    RoutingStatus.MX_TARGET_IS_CNAME.value
                    if cname_detected and prov == "UNCLASSIFIED"
                    else RoutingStatus.MX_RESOLVED.value
                )
                result = {
                    "status": status,
                    "primary_host": primary,
                    "all_hosts": resolvable_v4,
                    "inferred_provider": prov,
                    "provider_evidence": ev,
                    "ttl": bounded_ttl,
                    "retryable": False,
                }
                return _cache_and_return(clean, result, use_cache)

            if ipv6_only:
                primary = ipv6_only[0]
                prov, ev = infer_edge_provider(primary)
                result = {
                    "status": RoutingStatus.IPV6_ONLY_UNREACHABLE.value,
                    "primary_host": primary,
                    "all_hosts": ipv6_only,
                    "inferred_provider": prov,
                    "provider_evidence": ev,
                    "ttl": bounded_ttl,
                    "retryable": False,
                }
                return _cache_and_return(clean, result, use_cache)

            if had_transient:
                return {
                    "status": RoutingStatus.DNS_TEMPORARY_FAILURE.value,
                    "primary_host": None,
                    "all_hosts": [],
                    "inferred_provider": "UNCLASSIFIED",
                    "provider_evidence": None,
                    "ttl": 300,
                    "retryable": True,
                }

            result = {
                "status": RoutingStatus.MX_HOST_UNRESOLVABLE.value,
                "primary_host": None,
                "all_hosts": [],
                "inferred_provider": "UNCLASSIFIED",
                "provider_evidence": None,
                "ttl": 3600,
                "retryable": False,
            }
            return _cache_and_return(clean, result, use_cache)

    except Exception:
        return {
            "status": RoutingStatus.DNS_TEMPORARY_FAILURE.value,
            "primary_host": None,
            "all_hosts": [],
            "inferred_provider": "UNCLASSIFIED",
            "provider_evidence": None,
            "ttl": 300,
            "retryable": True,
        }

    # RFC 5321 §5.1 Implicit A/AAAA Fallback when no MX record exists
    if not allow_implicit_fallback:
        return {
            "status": RoutingStatus.NO_MAIL_ROUTE.value,
            "primary_host": None,
            "all_hosts": [],
            "inferred_provider": "UNCLASSIFIED",
            "provider_evidence": None,
            "ttl": 3600,
            "retryable": False,
        }

    has_v4, has_v6, _, trans = host_has_ip_address(clean)
    if has_v4 or has_v6:
        result = {
            "status": RoutingStatus.NO_MX_IMPLICIT_FALLBACK.value,
            "primary_host": clean,
            "all_hosts": [clean],
            "inferred_provider": "UNCLASSIFIED",
            "provider_evidence": clean,
            "ttl": 3600,
            "retryable": False,
        }
        return _cache_and_return(clean, result, use_cache)

    if trans:
        return {
            "status": RoutingStatus.DNS_TEMPORARY_FAILURE.value,
            "primary_host": None,
            "all_hosts": [],
            "inferred_provider": "UNCLASSIFIED",
            "provider_evidence": None,
            "ttl": 300,
            "retryable": True,
        }

    result = {
        "status": RoutingStatus.NO_MAIL_ROUTE.value,
        "primary_host": None,
        "all_hosts": [],
        "inferred_provider": "UNCLASSIFIED",
        "provider_evidence": None,
        "ttl": 3600,
        "retryable": False,
    }
    return _cache_and_return(clean, result, use_cache)


def _cache_and_return(domain: str, result: dict, use_cache: bool) -> dict:
    if use_cache and not result.get("retryable"):
        ttl = int(result.get("ttl", 3600))
        conn = sqlite3.connect(DNS_CACHE_DB)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO dns_cache (domain, payload_json, expires_at) VALUES (?, ?, ?)",
                (domain, json.dumps(result), time.time() + ttl),
            )
            conn.commit()
        finally:
            conn.close()
    return result


def resolve_company_entity(board: ATSBoard, jobs: list[ATSJob] | None = None) -> CompanyRecord:
    """
    Combines Gate 2A (Canonical Domain Resolution) and Gate 2B (Audited DNS Routing),
    including automatic split-domain TLD recovery if the marketing domain has NULL_MX/NO_MAIL_ROUTE.
    """
    primary_domain, company_name = resolve_canonical_domain(board, jobs)
    candidates = generate_split_domain_candidates(board.slug, primary_domain)

    best_record: CompanyRecord | None = None
    for domain_candidate in candidates[:3]:
        routing = resolve_domain_mail_routing(domain_candidate)
        status_enum = RoutingStatus(routing["status"])
        record = CompanyRecord(
            canonical_domain=domain_candidate,
            canonical_name=company_name,
            routing_status=status_enum,
            primary_mx_host=routing.get("primary_host"),
            all_mx_hosts=tuple(routing.get("all_hosts", ())),
            inferred_provider=routing.get("inferred_provider", "UNCLASSIFIED"),
            provider_evidence=routing.get("provider_evidence"),
            dns_ttl=int(routing.get("ttl", 3600)),
            retryable=bool(routing.get("retryable", False)),
        )
        if status_enum == RoutingStatus.MX_RESOLVED:
            return record
        if best_record is None:
            best_record = record

    return best_record or CompanyRecord(canonical_domain=primary_domain, canonical_name=company_name)
