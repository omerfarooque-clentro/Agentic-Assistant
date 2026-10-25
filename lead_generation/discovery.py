from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import unquote, urlencode, urlparse

import httpx
from ddgs import DDGS

from .models import ATSBoard, MAX_CAMPAIGN_QUOTA

# ---------------------------------------------------------------------------
# 1. Circuit Breaker Configuration & Thread-Safe Health State
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreaker:
    failure_threshold: int = 2
    cooldown_seconds: float = 600.0  # 10 minutes quarantine when tripped
    consecutive_failures: int = 0
    tripped_until: float = 0.0

    def is_available(self) -> bool:
        if self.tripped_until == 0.0:
            return True
        if time.monotonic() > self.tripped_until:
            return True  # Half-open: allow one trial probe
        return False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.tripped_until = 0.0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.tripped_until = time.monotonic() + self.cooldown_seconds


CIRCUIT_BREAKERS: dict[str, CircuitBreaker] = {
    "duckduckgo": CircuitBreaker(failure_threshold=2, cooldown_seconds=300.0),
    "duckduckgo_html": CircuitBreaker(failure_threshold=2, cooldown_seconds=300.0),
    "searxng": CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0),
    "brave_html": CircuitBreaker(failure_threshold=2, cooldown_seconds=600.0),
    "yahoo_html": CircuitBreaker(failure_threshold=2, cooldown_seconds=600.0),
    "serper": CircuitBreaker(failure_threshold=2, cooldown_seconds=1200.0),
    "brave": CircuitBreaker(failure_threshold=2, cooldown_seconds=1200.0),
    "tavily": CircuitBreaker(failure_threshold=2, cooldown_seconds=1200.0),
    "google": CircuitBreaker(failure_threshold=2, cooldown_seconds=3600.0),
}

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
)
_ua_index = 0
_ua_lock = threading.Lock()


def _next_user_agent() -> str:
    global _ua_index
    with _ua_lock:
        ua = USER_AGENTS[_ua_index % len(USER_AGENTS)]
        _ua_index += 1
        return ua

# ---------------------------------------------------------------------------
# 2. SQLite Persistent SERP Cache Layer (Zero Rate-Limiting on Repeat Queries)
# ---------------------------------------------------------------------------

CACHE_DB_PATH = os.path.join(os.path.dirname(__file__), ".serp_cache.db")


def _init_cache_db() -> None:
    conn = sqlite3.connect(CACHE_DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS serp_cache (
                query_hash TEXT PRIMARY KEY,
                query_text TEXT,
                results_json TEXT,
                expires_at REAL
            )
        """)
        conn.commit()
    finally:
        conn.close()


_init_cache_db()


def get_cached_search(query: str) -> list[dict] | None:
    q_hash = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()
    now = time.time()
    conn = sqlite3.connect(CACHE_DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT results_json FROM serp_cache WHERE query_hash = ? AND expires_at > ?",
            (q_hash, now),
        )
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                return None
    finally:
        conn.close()
    return None


def set_cached_search(query: str, results: list[dict], ttl_seconds: float = 432000.0) -> None:
    q_hash = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()
    expires_at = time.time() + ttl_seconds
    conn = sqlite3.connect(CACHE_DB_PATH)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO serp_cache (query_hash, query_text, results_json, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (q_hash, query.strip().lower(), json.dumps(results), expires_at),
        )
        conn.commit()
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# 3. Free & Freemium Search Provider Implementations
# ---------------------------------------------------------------------------

def _strip_tags(raw_html: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", raw_html)
    return html.unescape(" ".join(cleaned.split()))


def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    """Tier 1A: Keyless DuckDuckGo via ddgs client."""
    with DDGS() as search:
        raw = list(search.text(query, max_results=max_results))
        return [
            {
                "href": r.get("href", ""),
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
            if r.get("href")
        ]


def _search_duckduckgo_html(query: str, max_results: int) -> list[dict]:
    """Tier 1B: Keyless Direct DuckDuckGo HTML endpoint fallback."""
    headers = {
        "User-Agent": _next_user_agent(),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        resp = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.text

    results: list[dict] = []
    # Match result links and snippets from DDG HTML layout
    link_pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    links = link_pattern.findall(body)
    snippets = snippet_pattern.findall(body)

    for idx, (raw_href, raw_title) in enumerate(links[:max_results]):
        href = html.unescape(raw_href)
        if "uddg=" in href:
            match = re.search(r"uddg=([^&]+)", href)
            if match:
                href = unquote(match.group(1))
        snippet = _strip_tags(snippets[idx]) if idx < len(snippets) else ""
        if href.startswith("http"):
            results.append({
                "href": href,
                "title": _strip_tags(raw_title),
                "snippet": snippet,
            })
    return results


SEARXNG_PUBLIC_INSTANCES = (
    "https://priv.au/search",
    "https://search.ononoki.org/search",
    "https://searx.be/search",
    "https://paulgo.io/search",
)


def _search_searxng(query: str, max_results: int) -> list[dict]:
    """Tier 1C: Keyless SearXNG Metasearch multi-instance pool."""
    custom_url = os.getenv("SEARXNG_URL")
    instances = (custom_url, *SEARXNG_PUBLIC_INSTANCES) if custom_url else SEARXNG_PUBLIC_INSTANCES
    params = {"q": query, "format": "json", "language": "en"}

    for instance_url in instances:
        if not instance_url:
            continue
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                resp = client.get(
                    instance_url,
                    params=params,
                    headers={"User-Agent": _next_user_agent()},
                )
                resp.raise_for_status()
                data = resp.json().get("results", [])
                if data:
                    return [
                        {
                            "href": item.get("url", ""),
                            "title": item.get("title", ""),
                            "snippet": item.get("content", ""),
                        }
                        for item in data[:max_results]
                        if item.get("url")
                    ]
        except Exception:
            continue
    return []


def _search_brave_html(query: str, max_results: int) -> list[dict]:
    """Tier 1D: Keyless Brave Web Search HTML parser."""
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        resp = client.get(
            "https://search.brave.com/search",
            params={"q": query, "source": "web"},
            headers={"User-Agent": _next_user_agent(), "Accept-Language": "en-US,en;q=0.9"},
        )
        resp.raise_for_status()
        body = resp.text

    results: list[dict] = []
    # Extract external links from Brave web results
    pattern = re.compile(
        r'<a[^>]+href="(https?://(?!(?:[^"/]*\.)?brave\.com)[^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    seen: set[str] = set()
    for raw_url, raw_title in pattern.findall(body):
        url = html.unescape(raw_url)
        title = _strip_tags(raw_title)
        if url not in seen and title and len(title) > 3:
            seen.add(url)
            results.append({"href": url, "title": title, "snippet": title})
            if len(results) >= max_results:
                break
    return results


def _search_yahoo_html(query: str, max_results: int) -> list[dict]:
    """Tier 1E: Keyless Yahoo Search HTML fallback."""
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        resp = client.get(
            "https://search.yahoo.com/search",
            params={"p": query, "n": min(max_results, 20)},
            headers={"User-Agent": _next_user_agent()},
        )
        resp.raise_for_status()
        body = resp.text

    results: list[dict] = []
    seen: set[str] = set()
    # Yahoo wraps outbound links in RU=.../RK= or direct hrefs
    for match in re.finditer(r'RU=(https?[^/]+)/RK=', body):
        url = unquote(match.group(1))
        if "yahoo.com" not in url and "bing.com" not in url and url not in seen:
            seen.add(url)
            results.append({"href": url, "title": url, "snippet": ""})
            if len(results) >= max_results:
                break
    return results


def _search_serper(query: str, max_results: int) -> list[dict]:
    """Tier 2A: Serper.dev (2,500 free searches) when SERPER_API_KEY is configured."""
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return []
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": min(max_results, 20)},
        )
        resp.raise_for_status()
        organic = resp.json().get("organic", [])
        return [
            {
                "href": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in organic
        ]


def _search_brave_api(query: str, max_results: int) -> list[dict]:
    """Tier 2B: Brave Search API (2,000 free/month) when BRAVE_SEARCH_API_KEY is configured."""
    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        return []
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            params={"q": query, "count": min(max_results, 20)},
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [
            {
                "href": item.get("url", ""),
                "title": item.get("title", ""),
                "snippet": item.get("description", ""),
            }
            for item in results
        ]


def _search_tavily(query: str, max_results: int) -> list[dict]:
    """Tier 2C: Tavily API when TAVILY_API_KEY is configured."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return []
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": max_results},
        )
        resp.raise_for_status()
        return [
            {
                "href": item.get("url", ""),
                "title": item.get("title", ""),
                "snippet": item.get("content", ""),
            }
            for item in resp.json().get("results", [])
        ]


def _search_google_cse(query: str, max_results: int) -> list[dict]:
    """Tier 2D: Google Custom Search API when GOOGLE_API_KEY & GOOGLE_CSE_ID are configured."""
    api_key = os.getenv("GOOGLE_API_KEY")
    search_engine_id = os.getenv("GOOGLE_CSE_ID")
    if not api_key or not search_engine_id:
        return []
    query_string = urlencode({
        "key": api_key,
        "cx": search_engine_id,
        "q": query,
        "num": min(max_results, 10),
    })
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(f"https://www.googleapis.com/customsearch/v1?{query_string}")
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            {
                "href": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in items
        ]

# ---------------------------------------------------------------------------
# 4. Zero-Rate-Limit Multi-Provider Dispatcher
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY = {
    "duckduckgo": _search_duckduckgo,
    "duckduckgo_html": _search_duckduckgo_html,
    "searxng": _search_searxng,
    "brave_html": _search_brave_html,
    "yahoo_html": _search_yahoo_html,
    "serper": _search_serper,
    "brave": _search_brave_api,
    "tavily": _search_tavily,
    "google": _search_google_cse,
}

DEFAULT_PROVIDER_CASCADE = (
    "duckduckgo",
    "duckduckgo_html",
    "searxng",
    "brave_html",
    "yahoo_html",
    "serper",
    "brave",
    "tavily",
    "google",
)


def search_web(
    query: str,
    max_results: int = 20,
    providers: Iterable[str] = DEFAULT_PROVIDER_CASCADE,
    use_cache: bool = True,
) -> list[dict]:
    """
    Executes instant multi-provider web search without blocking rate-limit sleeps.
    1. Checks persistent SQLite cache first.
    2. Rotates across free & freemium search engines, skipping tripped circuit breakers.
    """
    if use_cache:
        cached = get_cached_search(query)
        if cached is not None:
            return cached

    for provider_name in providers:
        breaker = CIRCUIT_BREAKERS.get(provider_name)
        if breaker and not breaker.is_available():
            continue

        engine_func = PROVIDER_REGISTRY.get(provider_name)
        if not engine_func:
            continue

        try:
            results = engine_func(query, max_results)
            if results:
                if breaker:
                    breaker.record_success()
                if use_cache:
                    set_cached_search(query, results)
                return results
        except Exception:
            if breaker:
                breaker.record_failure()
            continue

    return []

# ---------------------------------------------------------------------------
# 5. Dork Compression, Reserved Slug Guard & Zero-Search Curated Seed Catalog
# ---------------------------------------------------------------------------

ATS_SLUG_PATTERNS = {
    "lever": re.compile(r"jobs\.lever\.co/([^/?#]+)", re.IGNORECASE),
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)", re.IGNORECASE),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)", re.IGNORECASE),
    "workable": re.compile(r"apply\.workable\.com/([^/?#]+)", re.IGNORECASE),
    "breezy": re.compile(r"([^./?#]+)\.breezy\.hr", re.IGNORECASE),
}

ATS_RESERVED_SLUGS = {
    "search", "about", "privacy", "terms", "jobs", "careers",
    "apply", "default", "api", "v1", "v0", "postings", "browse",
    "widget", "embed", "login", "signin", "help", "contact", "legal",
    "blog", "press", "security", "status", "docs", "support",
}

# Verified Curated ATS Seed Catalog (SMB / Series-A / Remote-First Startups across Global Hubs)
KNOWN_ATS_SEEDS: dict[str, tuple[tuple[str, str], ...]] = {
    "united kingdom": (
        ("lever", "skyralio"),
        ("ashby", "posthog"),
        ("ashby", "synthesia"),
        ("ashby", "cleo"),
        ("ashby", "primer"),
        ("greenhouse", "paddle"),
        ("greenhouse", "tractable"),
        ("greenhouse", "signalai"),
        ("greenhouse", "tessian"),
        ("greenhouse", "zego"),
        ("greenhouse", "bloomandwild"),
        ("greenhouse", "lyst"),
        ("ashby", "omnipresent"),
        ("greenhouse", "huma"),
        ("greenhouse", "complyadvantage"),
        ("greenhouse", "currencycloud"),
        ("greenhouse", "motorway"),
        ("greenhouse", "faculty"),
        ("ashby", "incidentio"),
        ("ashby", "griffin"),
        ("ashby", "speakeasy"),
        ("greenhouse", "marshmallow"),
        ("greenhouse", "thoughtmachine"),
        ("greenhouse", "graphcore"),
    ),
    "australia": (
        ("greenhouse", "safetyculture"),
        ("greenhouse", "cultureamp"),
        ("ashby", "immutable"),
        ("greenhouse", "employmenthero"),
        ("greenhouse", "rokt"),
        ("greenhouse", "deputy"),
        ("greenhouse", "envato"),
        ("greenhouse", "finder"),
        ("greenhouse", "go1"),
        ("greenhouse", "megaport"),
        ("greenhouse", "siteminder"),
        ("greenhouse", "linktree"),
        ("greenhouse", "mryum"),
        ("greenhouse", "octopusdeploy"),
        ("greenhouse", "securecodewarrior"),
        ("greenhouse", "tyropayments"),
        ("greenhouse", "whispir"),
        ("ashby", "eucalyptus"),
        ("greenhouse", "morsemicro"),
        ("greenhouse", "forage"),
        ("breezy", "propic"),
        ("workable", "endaoment"),
        ("greenhouse", "rokid"),
    ),
    "united states": (
        ("ashby", "linear"),
        ("greenhouse", "tailscale"),
        ("ashby", "elevenlabs"),
        ("breezy", "skyspecs"),
        ("ashby", "augmodo"),
        ("ashby", "supabase"),
        ("ashby", "resend"),
        ("ashby", "neon"),
        ("greenhouse", "planetscale"),
        ("greenhouse", "temporal"),
        ("greenhouse", "pulumi"),
        ("ashby", "pinecone"),
        ("ashby", "cursor"),
        ("ashby", "langfuse"),
        ("ashby", "modal"),
        ("ashby", "baseten"),
        ("ashby", "inngest"),
        ("ashby", "calcom"),
        ("ashby", "dub"),
        ("ashby", "tinybird"),
        ("ashby", "turso"),
        ("ashby", "convex"),
        ("ashby", "mintlify"),
        ("greenhouse", "wandb"),
        ("greenhouse", "huggingface"),
        ("greenhouse", "dbtlabs"),
        ("greenhouse", "launchdarkly"),
        ("greenhouse", "retool"),
        ("greenhouse", "webflow"),
        ("greenhouse", "zapier"),
        ("greenhouse", "vanta"),
        ("lever", "mistral"),
    ),
    "united arab emirates": (
        ("greenhouse", "careem"),
        ("ashby", "tabby"),
        ("greenhouse", "tamara"),
        ("workable", "huspy"),
        ("workable", "sarwa"),
        ("ashby", "ziina"),
        ("workable", "baraka"),
        ("ashby", "leantech"),
        ("workable", "nybl"),
        ("ashby", "omnipresent"),
        ("greenhouse", "remote"),
        ("greenhouse", "deel"),
        ("ashby", "posthog"),
        ("ashby", "supabase"),
        ("greenhouse", "tailscale"),
    ),
    "canada": (
        ("greenhouse", "tailscale"),
        ("greenhouse", "cohere"),
        ("ashby", "supabase"),
        ("ashby", "linear"),
        ("ashby", "posthog"),
        ("greenhouse", "zapier"),
        ("greenhouse", "temporal"),
        ("greenhouse", "pulumi"),
        ("ashby", "resend"),
        ("ashby", "neon"),
        ("breezy", "skyspecs"),
    ),
    "europe": (
        ("ashby", "posthog"),
        ("lever", "mistral"),
        ("ashby", "elevenlabs"),
        ("ashby", "langfuse"),
        ("ashby", "qdrant"),
        ("greenhouse", "weaviate"),
        ("ashby", "tinybird"),
        ("ashby", "calcom"),
        ("ashby", "omnipresent"),
        ("ashby", "synthesia"),
        ("ashby", "primer"),
        ("greenhouse", "remote"),
        ("greenhouse", "deel"),
        ("greenhouse", "paddle"),
    ),
    "singapore": (
        ("ashby", "supabase"),
        ("greenhouse", "airwallex"),
        ("ashby", "immutable"),
        ("greenhouse", "go1"),
        ("greenhouse", "siteminder"),
        ("greenhouse", "remote"),
        ("greenhouse", "deel"),
    ),
    "remote": (
        ("ashby", "posthog"),
        ("ashby", "supabase"),
        ("ashby", "linear"),
        ("greenhouse", "tailscale"),
        ("ashby", "elevenlabs"),
        ("ashby", "resend"),
        ("ashby", "neon"),
        ("ashby", "calcom"),
        ("ashby", "dub"),
        ("ashby", "inngest"),
        ("ashby", "tinybird"),
        ("ashby", "turso"),
        ("greenhouse", "zapier"),
        ("greenhouse", "remote"),
        ("greenhouse", "deel"),
        ("greenhouse", "pulumi"),
        ("greenhouse", "temporal"),
        ("ashby", "omnipresent"),
        ("breezy", "skyspecs"),
        ("ashby", "augmodo"),
    ),
    "default": (
        ("ashby", "posthog"),
        ("ashby", "supabase"),
        ("ashby", "linear"),
        ("greenhouse", "tailscale"),
        ("ashby", "elevenlabs"),
        ("breezy", "skyspecs"),
        ("ashby", "augmodo"),
        ("lever", "skyralio"),
        ("ashby", "resend"),
        ("ashby", "neon"),
        ("greenhouse", "planetscale"),
        ("greenhouse", "temporal"),
        ("greenhouse", "pulumi"),
        ("ashby", "pinecone"),
        ("ashby", "cursor"),
        ("ashby", "langfuse"),
        ("ashby", "modal"),
        ("ashby", "baseten"),
        ("ashby", "inngest"),
        ("ashby", "calcom"),
        ("ashby", "dub"),
        ("ashby", "tinybird"),
        ("ashby", "turso"),
        ("ashby", "convex"),
        ("ashby", "mintlify"),
        ("lever", "mistral"),
        ("ashby", "synthesia"),
        ("ashby", "cleo"),
        ("ashby", "primer"),
        ("greenhouse", "paddle"),
        ("greenhouse", "tractable"),
        ("greenhouse", "signalai"),
        ("greenhouse", "tessian"),
        ("greenhouse", "huma"),
        ("greenhouse", "complyadvantage"),
        ("greenhouse", "faculty"),
        ("greenhouse", "safetyculture"),
        ("greenhouse", "cultureamp"),
        ("ashby", "immutable"),
        ("greenhouse", "forage"),
        ("breezy", "propic"),
        ("workable", "endaoment"),
    ),
}


def normalize_location(location: str) -> str:
    loc = location.strip().replace('"', '')
    upper = loc.upper()
    if upper in {"UK", "U.K.", "GB", "GREAT BRITAIN", "ENGLAND", "LONDON"}:
        return "United Kingdom"
    if upper in {"AU", "AUS", "AUSTRALIA", "NZ", "NEW ZEALAND"}:
        return "Australia"
    if upper in {"US", "U.S.", "USA", "U.S.A.", "UNITED STATES"}:
        return "United States"
    if upper in {"UAE", "U.A.E.", "DUBAI", "ABU DHABI", "UNITED ARAB EMIRATES", "MENA", "KSA", "SAUDI ARABIA"}:
        return "United Arab Emirates"
    if upper in {"CA", "CANADA", "TORONTO", "VANCOUVER", "MONTREAL"}:
        return "Canada"
    if upper in {
        "EU", "EUROPE", "DE", "GERMANY", "BERLIN", "NL", "NETHERLANDS", "AMSTERDAM",
        "IE", "IRELAND", "DUBLIN", "CH", "SWITZERLAND", "ZURICH", "FR", "FRANCE", "PARIS",
        "ES", "SPAIN", "BARCELONA", "MADRID", "PT", "PORTUGAL", "LISBON", "SE", "SWEDEN",
        "STOCKHOLM", "EE", "ESTONIA", "PL", "POLAND", "AT", "AUSTRIA", "DK", "DENMARK",
        "NO", "NORWAY", "FI", "FINLAND",
    }:
        return "Europe"
    if upper in {"SG", "SINGAPORE", "HK", "HONG KONG", "APAC"}:
        return "Singapore"
    if upper in {"REMOTE", "WORLDWIDE", "GLOBAL", "ANYWHERE", "WFH", "WFA"}:
        return "Remote"
    return loc


def build_search_dorks(role: str, location: str) -> dict[str, str]:
    """Standard per-platform search dorks."""
    role = role.strip().replace('"', '')
    location = normalize_location(location)
    return {
        "lever": f'site:jobs.lever.co "{role}" "{location}"',
        "greenhouse": f'site:job-boards.greenhouse.io "{role}" "{location}"',
        "ashby": f'site:jobs.ashbyhq.com "{role}" "{location}"',
        "workable": f'site:apply.workable.com "{role}" "{location}"',
        "breezy": f'site:*.breezy.hr "{role}" "{location}"',
    }


def build_composite_search_dorks(role: str, location: str) -> list[str]:
    """Compresses 5 separate platform queries into 2 composite boolean dorks."""
    clean_role = role.strip().replace('"', '')
    clean_loc = normalize_location(location)
    return [
        f'(site:jobs.lever.co OR site:job-boards.greenhouse.io OR site:jobs.ashbyhq.com) "{clean_role}" "{clean_loc}"',
        f'(site:apply.workable.com OR site:*.breezy.hr) "{clean_role}" "{clean_loc}"',
    ]


def extract_ats_slug(url: str) -> tuple[str | None, str | None]:
    if not url:
        return None, None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    hostname = (parsed.hostname or "").lower()
    path = [part for part in parsed.path.split("/") if part]

    platform: str | None = None
    slug: str | None = None

    if hostname == "jobs.lever.co" and path:
        platform, slug = "lever", path[0].lower()
    elif hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and path:
        platform, slug = "greenhouse", path[0].lower()
    elif hostname == "jobs.ashbyhq.com" and path:
        platform, slug = "ashby", path[0].lower()
    elif hostname == "apply.workable.com" and path:
        platform, slug = "workable", path[0].lower()
    else:
        match = re.fullmatch(r"([^.]+)\.breezy\.hr", hostname)
        if match:
            platform, slug = "breezy", match.group(1).lower()

    if platform and slug:
        if slug in ATS_RESERVED_SLUGS or slug.startswith("www") or len(slug) < 2:
            return None, None
        return platform, slug
    return None, None


def get_curated_seeds(location: str) -> set[tuple[str, str]]:
    norm = normalize_location(location).lower()
    seeds = set(KNOWN_ATS_SEEDS.get(norm, ()))
    # Supplement with default seeds if regional seed count is small
    if not seeds or len(seeds) < 25:
        seeds.update(KNOWN_ATS_SEEDS["default"])
    return seeds


def discover_company_slugs(
    role: str,
    location: str,
    max_results: int = 50,
    use_curated_fallback: bool = True,
) -> set[tuple[str, str]]:
    """
    Discovers unique (platform, slug) pairs using compressed dorks across the free search cascade.
    Automatically supplements with curated ATS seeds if live search returns fewer candidate boards.
    """
    seeds: set[tuple[str, str]] = set()
    capped_results = min(max_results, MAX_CAMPAIGN_QUOTA)

    for dork in build_composite_search_dorks(role, location):
        results = search_web(dork, max_results=capped_results)
        for item in results:
            platform, slug = extract_ats_slug(item.get("href", ""))
            if platform and slug:
                seeds.add((platform, slug))

    # Fallback to individual platform dorks if boolean OR was unsupported by the active engine
    if not seeds:
        for dork in build_search_dorks(role, location).values():
            for item in search_web(dork, max_results=min(15, capped_results)):
                platform, slug = extract_ats_slug(item.get("href", ""))
                if platform and slug:
                    seeds.add((platform, slug))

    if use_curated_fallback and len(seeds) < capped_results:
        seeds.update(get_curated_seeds(location))

    return seeds


def build_board_url(platform: str, slug: str) -> ATSBoard | None:
    if not platform or not slug or slug.lower() in ATS_RESERVED_SLUGS:
        return None
    slug = slug.lower()
    endpoints = {
        "lever": (
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            f"https://jobs.lever.co/{slug}",
        ),
        "greenhouse": (
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            f"https://boards.greenhouse.io/{slug}",
        ),
        "ashby": (
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            f"https://jobs.ashbyhq.com/{slug}",
        ),
        "workable": (
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
            f"https://apply.workable.com/{slug}",
        ),
        "breezy": (
            f"https://{slug}.breezy.hr/json",
            f"https://{slug}.breezy.hr",
        ),
    }
    if platform not in endpoints:
        return None
    api_url, careers_url = endpoints[platform]
    return ATSBoard(platform=platform, slug=slug, api_url=api_url, careers_page_url=careers_url)
