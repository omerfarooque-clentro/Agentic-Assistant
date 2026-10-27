from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .discovery import search_web
from .models import (
    AttributionStatus,
    CompanyRecord,
    ContactObservation,
    ExecutiveIdentity,
    IdentityConfidence,
)

# ---------------------------------------------------------------------------
# Gate 3: Honest Executive Identity Resolution (Zero Fake/Guessed Links)
# ---------------------------------------------------------------------------

NEGATIVE_TENURE_PATTERN = re.compile(
    r"\b(?:former|ex-|previous|previously|past|retired|departed|outgoing|"
    r"advisor|advisory|board\s+member|non-executive|investor|intern|"
    r"assistant\s+to|chief\s+of\s+staff\s+to)\b",
    re.IGNORECASE,
)

HONORIFICS_AND_CREDENTIALS = re.compile(
    r"\b(?:dr|prof|mr|mrs|ms|mx|sir|dame|jr|sr|ii|iii|iv|phd|mba|msc|bsc|obe|cbe|mbe|fca|cfa)\b\.?",
    re.IGNORECASE,
)

ROLE_PRIORITY_RULES: list[tuple[re.Pattern, str, int]] = [
    (re.compile(r"\b(?:co-?founder\s*(?:&|and)\s*ceo|ceo\s*(?:&|and)\s*co-?founder|founder\s*(?:&|and)\s*ceo|ceo\s*(?:&|and)\s*founder)\b", re.I), "Co-Founder & CEO", 1),
    (re.compile(r"\b(?:chief\s+executive\s+officer|ceo)\b", re.I), "CEO", 2),
    (re.compile(r"\b(?:co-?founder\s*(?:&|and)\s*cto|cto\s*(?:&|and)\s*co-?founder)\b", re.I), "Co-Founder & CTO", 3),
    (re.compile(r"\b(?:chief\s+technology\s+officer|cto)\b", re.I), "CTO", 4),
    (re.compile(r"\b(?:co-?founder|founder)\b", re.I), "Co-Founder", 5),
    (re.compile(r"\b(?:vp\s+of\s+engineering|vp\s+engineering|vice\s+president\s+of\s+engineering|head\s+of\s+engineering)\b", re.I), "VP of Engineering", 6),
    (re.compile(r"\b(?:managing\s+director|president)\b", re.I), "Managing Director", 7),
]

LINKEDIN_PROFILE_URL_REGEX = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([a-zA-Z0-9\-_%]{3,100})/?",
    re.IGNORECASE,
)


def clean_linkedin_profile_url(url: str) -> str | None:
    """
    Validates and normalizes a LinkedIn personal profile URL (/in/<slug>).
    Rejects directory pages (/in/dir/), company pages (/company/), posts, or jobs.
    """
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return None
    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) < 2 or path_parts[0].lower() != "in":
        return None
    slug = path_parts[1].strip()
    if slug.lower() in {"dir", "pub", "jobs", "company", "feed", "learning", "pulse"} or len(slug) < 2:
        return None
    # Preserve regional subdomain (e.g. uk.linkedin.com) or default to www.linkedin.com
    return f"https://{host}/in/{slug}"


def normalize_person_name(raw_name: str) -> str:
    """
    Normalizes an executive's full name:
    - Strips emojis, parentheticals, credentials (MBA, PhD, Jr., OBE)
    - Transliterates accents/diacritics to ASCII (René -> Rene)
    """
    if not raw_name:
        return ""
    cleaned = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", raw_name)
    cleaned = cleaned.split(",")[0]
    cleaned = HONORIFICS_AND_CREDENTIALS.sub(" ", cleaned)
    normalized_unicode = unicodedata.normalize("NFKD", cleaned)
    ascii_name = normalized_unicode.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^a-zA-Z\s\-]", " ", ascii_name)
    tokens = [t.strip("-") for t in ascii_name.split() if len(t.strip("-")) >= 1]
    return " ".join(tokens)


def _looks_like_valid_person_name(name: str, company_name: str) -> bool:
    tokens = name.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    comp_lower = company_name.lower()
    disallowed_words = {
        "linkedin", "profile", "jobs", "careers", "hiring", "ceo", "cto",
        "founder", "co-founder", "president", "director", "engineering",
        "team", "about", "contact", "company", "login", "sign", comp_lower,
    }
    for t in tokens:
        if t.lower() in disallowed_words or len(t) < 2:
            return False
        if not re.match(r"^[A-Za-z][A-Za-z\-]*$", t):
            return False
    return True


def _company_matches_clause(company_name: str, text_clause: str) -> bool:
    """Checks if the target company name is genuinely present in a title or role clause."""
    company_tokens = re.findall(r"[a-z0-9]+", company_name.lower())
    if not company_tokens:
        return False

    candidates = {" ".join(company_tokens)}
    suffixes = (
        ("betting", "gaming"),
        ("technologies",),
        ("technology",),
        ("analytics",),
        ("industries",),
        ("solutions",),
        ("services",),
        ("systems",),
        ("labs",),
        ("lab",),
        ("space",),
        ("inc",),
        ("llc",),
        ("ltd",),
        ("limited",),
        ("corp",),
        ("corporation",),
        ("ai",),
    )
    while len(company_tokens) > 1:
        suffix = next(
            (item for item in suffixes if tuple(company_tokens[-len(item):]) == item),
            None,
        )
        if suffix is None or len(company_tokens) <= len(suffix):
            break
        company_tokens = company_tokens[:-len(suffix)]
        candidates.add(" ".join(company_tokens))

    clause = " ".join(re.findall(r"[a-z0-9]+", text_clause.lower()))
    return any(f" {candidate} " in f" {clause} " for candidate in candidates)


def _snippet_binds_role_to_company(snippet: str, company_name: str) -> bool:
    """
    Requires an explicit employment binding phrase in the snippet such as:
    'CEO at <Company>', 'Co-Founder & CEO of <Company>', 'Founder @ <Company>', 'CTO - <Company>'.
    Prevents false positives where another company's CEO merely mentions <Company> in passing.
    """
    comp_escaped = re.escape(company_name.strip())
    # Also allow the first word of multi-word company names (e.g. 'Monzo' for 'Monzo Bank')
    first_token = re.sub(r"[^a-zA-Z0-9]", "", company_name.split()[0]) if company_name.split() else ""
    comp_pattern = rf"(?:{comp_escaped}|{re.escape(first_token)})" if len(first_token) >= 4 else comp_escaped

    binding_regex = re.compile(
        rf"\b(?:ceo|chief\s+executive|cto|chief\s+technology|co-?founder|founder|"
        rf"managing\s+director|president|vp\s+(?:of\s+)?engineering|head\s+of\s+engineering)"
        rf"\b[^.;|\n]{{0,35}}?\b(?:at|@|of|for|-|–|,)\s*{comp_pattern}\b",
        re.IGNORECASE,
    )
    return bool(binding_regex.search(snippet))


def parse_linkedin_snippet(
    title: str,
    snippet: str,
    url: str,
    company_name: str,
) -> ExecutiveIdentity | None:
    """
    Strictly parses a public LinkedIn search result title & snippet into an ExecutiveIdentity.
    Enforces:
    1. Valid personal profile URL (`linkedin.com/in/<slug>`)
    2. Strict employer verification:
       - If the LinkedIn title has 3+ parts (`Name - Role - Company`), the Company part MUST match `company_name`.
       - Otherwise, the company name must be in the title OR explicitly bound to the executive role in the snippet (`CEO at <Company>`).
    3. Negative tenure filtering (flags 'Former', 'Ex-', 'Advisor')
    """
    clean_url = clean_linkedin_profile_url(url)
    if not clean_url:
        return None

    # LinkedIn titles follow: "Full Name - Title - Company | LinkedIn"
    cleaned_title = re.sub(r"\s*\|\s*LinkedIn.*$", "", title, flags=re.IGNORECASE).strip()
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", cleaned_title) if p.strip()]
    if not parts:
        return None

    candidate_name = normalize_person_name(parts[0])
    if not _looks_like_valid_person_name(candidate_name, company_name):
        return None

    # Guard against third-party executives (e.g., "Matt Ghering - CEO - OtherCompany")
    if len(parts) >= 3:
        # Check if the company is mentioned anywhere in the non-name title segments
        non_name_title = " ".join(parts[1:])
        if not _company_matches_clause(company_name, non_name_title):
            # The title explicitly names a DIFFERENT company as their employer!
            return None

    title_has_company = _company_matches_clause(company_name, cleaned_title)
    snippet_binds_company = _snippet_binds_role_to_company(snippet, company_name)

    if not title_has_company and not snippet_binds_company:
        # Reject loose mentions where the company name only appears randomly in the snippet body
        return None

    # Role matching must occur in the title or in the bound snippet clause
    combined = f"{cleaned_title} | {snippet}"
    matched_role: str | None = None
    priority_rank = 99
    for pattern, role_label, rank in ROLE_PRIORITY_RULES:
        if pattern.search(cleaned_title) or (snippet_binds_company and pattern.search(snippet)):
            matched_role = role_label
            priority_rank = rank
            break

    if not matched_role:
        return None

    has_negative_tenure = bool(NEGATIVE_TENURE_PATTERN.search(combined))
    is_truncated = cleaned_title.endswith("...") or cleaned_title.endswith("…")

    if has_negative_tenure or is_truncated:
        confidence = IdentityConfidence.AMBIGUOUS_NEEDS_REVIEW
        note = "Flagged for review due to former/ambiguous tenure or truncated headline."
    else:
        confidence = IdentityConfidence.CURRENT_CONFIRMED
        note = "Verified active executive from LinkedIn headline/employer clause."

    return ExecutiveIdentity(
        full_name=parts[0].strip(),
        normalized_name=candidate_name.lower(),
        title=matched_role,
        source_url=clean_url,
        confidence=confidence,
        observed_at=datetime.now(timezone.utc),
        priority_rank=priority_rank,
        notes=note,
    )


def scrape_linkedin_from_company_site(company: CompanyRecord) -> ExecutiveIdentity | None:
    """
    Tier 1 (Zero Search Engine Queries):
    Scrapes the company's own /about, /team, /leadership pages for direct <a href="...linkedin.com/in/...">
    links published by the company alongside executive titles.
    """
    if not company.canonical_domain:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    candidates: list[ExecutiveIdentity] = []

    for path in ("/about", "/team", "/about-us", "/leadership", "/company"):
        url = f"https://{company.canonical_domain}{path}"
        try:
            with httpx.Client(timeout=4.0, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code != 200 or "linkedin.com/in/" not in resp.text.lower():
                    continue
                text = resp.text
                for match in LINKEDIN_PROFILE_URL_REGEX.finditer(text):
                    li_url = clean_linkedin_profile_url(match.group(0))
                    if not li_url:
                        continue
                    start = max(0, match.start() - 350)
                    end = min(len(text), match.end() + 350)
                    window = html.unescape(re.sub(r"<[^>]+>", " ", text[start:end]))
                    window = " ".join(window.split())

                    matched_role: str | None = None
                    priority_rank = 99
                    for pattern, role_label, rank in ROLE_PRIORITY_RULES:
                        if pattern.search(window):
                            matched_role = role_label
                            priority_rank = rank
                            break
                    if not matched_role:
                        continue

                    # Derive name from LinkedIn slug if not cleanly isolated in window
                    slug = li_url.rstrip("/").split("/")[-1]
                    slug_clean = re.sub(r"-[0-9a-f]{5,}.*$", "", slug)
                    slug_parts = [p.capitalize() for p in slug_clean.split("-") if p.isalpha() and len(p) >= 2]
                    if len(slug_parts) >= 2:
                        derived_name = " ".join(slug_parts[:3])
                        if _looks_like_valid_person_name(derived_name, company.canonical_name):
                            candidates.append(
                                ExecutiveIdentity(
                                    full_name=derived_name,
                                    normalized_name=derived_name.lower(),
                                    title=matched_role,
                                    source_url=li_url,
                                    confidence=IdentityConfidence.CURRENT_CONFIRMED,
                                    priority_rank=priority_rank,
                                    notes=f"Extracted directly from first-party company page ({url}).",
                                )
                            )
        except Exception:
            continue

    if candidates:
        candidates.sort(key=lambda c: c.priority_rank)
        return candidates[0]
    return None


def resolve_executive_identity(
    company: CompanyRecord,
    enable_site_scrape: bool = False,
) -> ExecutiveIdentity:
    """
    Gate 3: Resolves the highest-ranking current executive (CEO > CTO > Co-Founder > VP Eng).
    1. Optionally checks the company's own /about & /team pages first (0 search engine queries).
    2. Runs targeted search dorks (`site:linkedin.com/in "<Company>" ...`) with strict employer clause validation.
    3. Never guesses or fabricates a LinkedIn URL. If unverified, returns UNRESOLVED.
    """
    if enable_site_scrape:
        site_exec = scrape_linkedin_from_company_site(company)
        if site_exec:
            return site_exec

    dork_queries = [
        f'site:linkedin.com/in "{company.canonical_name}" ("CEO" OR "Co-Founder" OR "Founder" OR "CTO")',
        f'ceo of "{company.canonical_name}" site:linkedin.com/in',
    ]

    candidates: list[ExecutiveIdentity] = []
    for query in dork_queries:
        results = search_web(query, max_results=10)
        for item in results:
            parsed = parse_linkedin_snippet(
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                url=item.get("href", ""),
                company_name=company.canonical_name,
            )
            if parsed:
                candidates.append(parsed)
        # Stop querying once we have a confirmed current executive
        if any(c.confidence == IdentityConfidence.CURRENT_CONFIRMED for c in candidates):
            break

    if candidates:
        candidates.sort(
            key=lambda c: (
                0 if c.confidence == IdentityConfidence.CURRENT_CONFIRMED else 1,
                c.priority_rank,
            )
        )
        return candidates[0]

    return ExecutiveIdentity(
        full_name="Unresolved Executive",
        normalized_name="unresolved executive",
        title="Unresolved",
        source_url="",
        confidence=IdentityConfidence.UNRESOLVED,
        priority_rank=99,
        notes="No verified LinkedIn profile matched employer criteria.",
    )


# ---------------------------------------------------------------------------
# Gate 4: 3-Tier Contact Waterfall & Role-Email Misattribution Guard
# ---------------------------------------------------------------------------

ROLE_MAILBOX_PREFIXES = {
    "info", "contact", "support", "careers", "jobs", "privacy", "legal",
    "dpo", "sales", "press", "billing", "security", "help", "admin",
    "hello", "office", "hr", "team", "media", "enquiries", "inquiries",
    "marketing", "compliance", "accounts", "finance", "feedback", "abuse",
    "postmaster", "webmaster", "hostmaster", "noreply", "no-reply",
}


def is_role_or_generic_email(email: str) -> bool:
    if "@" not in email:
        return True
    local_part = email.split("@", 1)[0].lower().strip()
    base_prefix = re.split(r"[._\-+]", local_part)[0]
    return local_part in ROLE_MAILBOX_PREFIXES or base_prefix in ROLE_MAILBOX_PREFIXES


def infer_email_pattern_from_samples(emails: list[str], domain: str) -> str | None:
    """
    Infers corporate email syntax pattern from non-role employee emails discovered on-domain:
    Returns one of: 'first.last', 'first_last', 'first', 'flast' or None.
    """
    dom = domain.lower()
    for email in emails:
        email_clean = email.lower().strip()
        if not email_clean.endswith(f"@{dom}") or is_role_or_generic_email(email_clean):
            continue
        local = email_clean.split("@", 1)[0]
        if "." in local:
            parts = local.split(".")
            if len(parts) == 2 and len(parts[0]) >= 2 and len(parts[1]) >= 2:
                return "first.last"
        elif "_" in local:
            parts = local.split("_")
            if len(parts) == 2 and len(parts[0]) >= 2 and len(parts[1]) >= 2:
                return "first_last"
    return None


def generate_executive_permutations(
    normalized_exec_name: str,
    domain: str,
    preferred_pattern: str | None = None,
    max_permutations: int = 4,
) -> list[str]:
    """
    Generates up to 4 bounded corporate email syntax permutations.
    Handles hyphenated and multi-part surnames cleanly (e.g., 'Naomi Hulme-Smith').
    """
    clean_name = normalize_person_name(normalized_exec_name).lower()
    tokens = [t for t in clean_name.split() if t]
    if not tokens or clean_name == "unresolved executive":
        return []

    first = re.sub(r"[^a-z]", "", tokens[0])
    raw_last = tokens[-1] if len(tokens) > 1 else ""
    last_no_hyphen = re.sub(r"[^a-z]", "", raw_last)
    last_parts = [re.sub(r"[^a-z]", "", p) for p in raw_last.split("-") if p]

    if not first:
        return []
    if not last_no_hyphen:
        return [f"{first}@{domain}"]

    f_initial = first[0]
    pattern_map = {
        "first.last": f"{first}.{last_no_hyphen}@{domain}",
        "first": f"{first}@{domain}",
        "flast": f"{f_initial}{last_no_hyphen}@{domain}",
        "first_last": f"{first}_{last_no_hyphen}@{domain}",
        "firstlast": f"{first}{last_no_hyphen}@{domain}",
    }

    ordered: list[str] = []
    if preferred_pattern and preferred_pattern in pattern_map:
        ordered.append(pattern_map[preferred_pattern])

    for default_key in ("first.last", "first", "flast", "first_last"):
        candidate = pattern_map[default_key]
        if candidate not in ordered:
            ordered.append(candidate)

    if len(last_parts) > 1 and len(last_parts[0]) >= 2:
        hyphen_variant = f"{first}.{last_parts[0]}@{domain}"
        if hyphen_variant not in ordered:
            ordered.insert(1, hyphen_variant)

    return ordered[:max_permutations]


def _email_matches_executive(email: str, normalized_exec_name: str) -> bool:
    if is_role_or_generic_email(email):
        return False
    tokens = [re.sub(r"[^a-z]", "", t) for t in normalized_exec_name.lower().split() if t]
    if not tokens:
        return False
    local = email.split("@", 1)[0].lower()
    first = tokens[0]
    last = re.sub(r"[^a-z]", "", tokens[-1]) if len(tokens) > 1 else ""
    return (first in local and len(first) >= 3) or (bool(last) and last in local and len(last) >= 3)


def scrape_on_domain_contacts(
    company: CompanyRecord,
    executive: ExecutiveIdentity,
    fetch_pages: bool = True,
) -> list[ContactObservation]:
    """
    Gate 4 Tier 1: Scrapes /about, /team, /contact, /privacy for DOM-attributed emails.
    Never assigns generic role emails (privacy@, info@) to the executive; uses non-matching
    employee emails strictly as CompanyFormatEvidence.
    """
    if not fetch_pages:
        return []

    domain = company.canonical_domain
    email_regex = re.compile(rf"[a-zA-Z0-9._%+\-]+@{re.escape(domain)}", re.IGNORECASE)
    observations: list[ContactObservation] = []
    discovered_emails: list[str] = []

    for path in ("/about", "/team", "/contact", "/privacy"):
        url = f"https://{domain}{path}"
        try:
            with httpx.Client(timeout=5.0, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
                found = {m.lower() for m in email_regex.findall(resp.text)}
                for em in found:
                    discovered_emails.append(em)
                    if _email_matches_executive(em, executive.normalized_name):
                        observations.append(
                            ContactObservation(
                                normalized_email=em,
                                discovery_tier="TIER_1_DOM",
                                attribution_status=AttributionStatus.PUBLISHED_DIRECT_MATCH,
                                source_url=url,
                            )
                        )
        except Exception:
            continue

    if not company.observed_email_pattern and discovered_emails:
        company.observed_email_pattern = infer_email_pattern_from_samples(discovered_emails, domain)

    return observations


def search_web_mentions_for_contact(
    company: CompanyRecord,
    executive: ExecutiveIdentity,
) -> list[ContactObservation]:
    """Gate 4 Tier 2: Searches public web mentions for '<Exec Name>' '@<domain>'."""
    if executive.confidence == IdentityConfidence.UNRESOLVED or executive.normalized_name == "unresolved executive":
        return []

    domain = company.canonical_domain
    query = f'"{executive.full_name}" "@{domain}"'
    results = search_web(query, max_results=5)
    email_regex = re.compile(rf"[a-zA-Z0-9._%+\-]+@{re.escape(domain)}", re.IGNORECASE)

    observations: list[ContactObservation] = []
    seen: set[str] = set()
    for item in results:
        text = f"{item.get('title', '')} {item.get('snippet', '')}"
        for match in email_regex.findall(text):
            em = match.lower()
            if em not in seen and _email_matches_executive(em, executive.normalized_name):
                seen.add(em)
                observations.append(
                    ContactObservation(
                        normalized_email=em,
                        discovery_tier="TIER_2_WEB_MENTION",
                        attribution_status=AttributionStatus.SEARCH_SNIPPET_UNVALIDATED,
                        source_url=item.get("href", ""),
                    )
                )
    return observations


def run_contact_waterfall(
    company: CompanyRecord,
    executive: ExecutiveIdentity,
    enable_dom_scrape: bool = False,
    enable_web_mentions: bool = True,
) -> list[ContactObservation]:
    """
    Executes the 3-Tier Contact Waterfall:
    Tier 1: On-domain DOM scrape (/about, /team, /contact, /privacy)
    Tier 2: Public search mentions ("<Name>" "@<domain>")
    Tier 3: Up to 4 bounded syntax permutations
    """
    observations: list[ContactObservation] = []

    if enable_dom_scrape:
        observations.extend(scrape_on_domain_contacts(company, executive, fetch_pages=True))

    if not observations and enable_web_mentions:
        observations.extend(search_web_mentions_for_contact(company, executive))

    # Tier 3: Bounded syntax permutations
    permutations = generate_executive_permutations(
        executive.normalized_name,
        company.canonical_domain,
        preferred_pattern=company.observed_email_pattern,
        max_permutations=4,
    )
    attr_status = (
        AttributionStatus.INFERRED_FORMAT_MATCH
        if company.observed_email_pattern
        else AttributionStatus.INFERRED_CANDIDATE
    )
    existing_emails = {obs.normalized_email for obs in observations}
    for perm in permutations:
        if perm not in existing_emails:
            observations.append(
                ContactObservation(
                    normalized_email=perm,
                    discovery_tier="TIER_3_PERMUTATION",
                    attribution_status=attr_status,
                    source_url=executive.source_url,
                )
            )

    return observations
