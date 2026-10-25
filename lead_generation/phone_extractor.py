from __future__ import annotations

import html
import json
import re
from typing import Any

import httpx
import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberMatcher

from .models import CompanyRecord

# ---------------------------------------------------------------------------
# Hybrid Phone Extraction: Schema.org JSON-LD + tel: Links + Google libphonenumber
# ---------------------------------------------------------------------------

TEL_HREF_REGEX = re.compile(
    r'href=["\']tel:([+\d\s().\-]{8,22})["\']',
    re.IGNORECASE,
)

JSON_LD_SCRIPT_REGEX = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

LABELED_PHONE_REGEX = re.compile(
    r"(?:tel(?:ephone)?|phone|call(?:\s+us)?|switchboard|office|hq|enquiries)\s*[:\-–]?\s*"
    r"((?:\+\d{1,3}[\s.\-]?(?:\(0\)[\s.\-]?)?|\b0|\(\d{2,3}\)\s?)\d{1,4}[\s.\-]?\d{3,4}[\s.\-]?\d{3,4})\b",
    re.IGNORECASE,
)

COUNTRY_TO_ISO_REGION: dict[str, str] = {
    "UK": "GB",
    "U.K.": "GB",
    "GB": "GB",
    "GREAT BRITAIN": "GB",
    "UNITED KINGDOM": "GB",
    "ENGLAND": "GB",
    "SCOTLAND": "GB",
    "LONDON": "GB",
    "MANCHESTER": "GB",
    "EDINBURGH": "GB",
    "AU": "AU",
    "AUS": "AU",
    "AUSTRALIA": "AU",
    "SYDNEY": "AU",
    "MELBOURNE": "AU",
    "BRISBANE": "AU",
    "NZ": "NZ",
    "NEW ZEALAND": "NZ",
    "AUCKLAND": "NZ",
    "US": "US",
    "U.S.": "US",
    "USA": "US",
    "U.S.A.": "US",
    "UNITED STATES": "US",
    "NEW YORK": "US",
    "SAN FRANCISCO": "US",
    "AUSTIN": "US",
    "SEATTLE": "US",
    "REMOTE": "US",
    "WORLDWIDE": "US",
    "GLOBAL": "US",
    "CA": "CA",
    "CANADA": "CA",
    "TORONTO": "CA",
    "VANCOUVER": "CA",
    "MONTREAL": "CA",
    "UAE": "AE",
    "U.A.E.": "AE",
    "UNITED ARAB EMIRATES": "AE",
    "DUBAI": "AE",
    "ABU DHABI": "AE",
    "MENA": "AE",
    "SAUDI ARABIA": "SA",
    "KSA": "SA",
    "RIYADH": "SA",
    "QATAR": "QA",
    "DOHA": "QA",
    "DE": "DE",
    "GERMANY": "DE",
    "BERLIN": "DE",
    "MUNICH": "DE",
    "NL": "NL",
    "NETHERLANDS": "NL",
    "AMSTERDAM": "NL",
    "IE": "IE",
    "IRELAND": "IE",
    "DUBLIN": "IE",
    "CH": "CH",
    "SWITZERLAND": "CH",
    "ZURICH": "CH",
    "FR": "FR",
    "FRANCE": "FR",
    "PARIS": "FR",
    "ES": "ES",
    "SPAIN": "ES",
    "MADRID": "ES",
    "BARCELONA": "ES",
    "PT": "PT",
    "PORTUGAL": "PT",
    "LISBON": "PT",
    "SE": "SE",
    "SWEDEN": "SE",
    "STOCKHOLM": "SE",
    "DK": "DK",
    "DENMARK": "DK",
    "COPENHAGEN": "DK",
    "NO": "NO",
    "NORWAY": "NO",
    "OSLO": "NO",
    "FI": "FI",
    "FINLAND": "FI",
    "HELSINKI": "FI",
    "EE": "EE",
    "ESTONIA": "EE",
    "TALLINN": "EE",
    "PL": "PL",
    "POLAND": "PL",
    "WARSAW": "PL",
    "AT": "AT",
    "AUSTRIA": "AT",
    "VIENNA": "AT",
    "BE": "BE",
    "BELGIUM": "BE",
    "BRUSSELS": "BE",
    "IT": "IT",
    "ITALY": "IT",
    "MILAN": "IT",
    "SG": "SG",
    "SINGAPORE": "SG",
    "HK": "HK",
    "HONG KONG": "HK",
    "JP": "JP",
    "JAPAN": "JP",
    "TOKYO": "JP",
    "IN": "IN",
    "INDIA": "IN",
    "BANGALORE": "IN",
    "BENGALURU": "IN",
    "BR": "BR",
    "BRAZIL": "BR",
    "MX": "MX",
    "MEXICO": "MX",
}

INVALID_PHONE_SUBSTRINGS = {
    "127.0.0.1", "0.0.0.0", "192.168", "10.0.", "255.255",
    "2020", "2021", "2022", "2023", "2024", "2025", "2026", "2027",
    "000000", "111111", "123456", "999999",
}


def _resolve_iso_region(default_country: str) -> str:
    upper = (default_country or "UK").upper().strip()
    if upper in COUNTRY_TO_ISO_REGION:
        return COUNTRY_TO_ISO_REGION[upper]
    for key, iso in COUNTRY_TO_ISO_REGION.items():
        if key in upper:
            return iso
    return "GB"


def normalize_phone_number(raw_phone: str, default_country: str = "UK") -> str | None:
    """
    Validates and normalizes a phone number using Google's `libphonenumber` (`phonenumbers` library).
    Rejects unallocated/invalid numbers (such as US 555-01xx fictive ranges, dates, or CSS numbers).
    """
    if not raw_phone:
        return None

    cleaned = html.unescape(raw_phone).strip()
    cleaned = re.sub(r"(?:ext|x|extension|#).*$", "", cleaned, flags=re.IGNORECASE).strip()

    for bad in INVALID_PHONE_SUBSTRINGS:
        if bad in cleaned:
            return None

    if re.search(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b", cleaned):
        return None

    iso_region = _resolve_iso_region(default_country)
    # Handle '+44 (0)20 ...' or '+61 (0)2 ...' parenthetical trunk zero before parsing
    cleaned_for_parse = re.sub(r"(\+\d{1,3})\s*\(0\)\s*", r"\1 ", cleaned)

    try:
        parsed = phonenumbers.parse(cleaned_for_parse, None if cleaned_for_parse.startswith("+") else iso_region)
        if not phonenumbers.is_valid_number(parsed):
            return None
        formatted = phonenumbers.format_number(parsed, PhoneNumberFormat.INTERNATIONAL)
        # Reject telecom-reserved fictive/TV drama ranges (US 555-01xx and UK Ofcom 020 7946 0xxx)
        if re.search(r"^\+1\s+\d{3}-555-01\d{2}$", formatted) or re.search(r"^\+44\s+20\s+7946\s+0\d{3}$", formatted):
            return None
        return formatted
    except phonenumbers.NumberParseException:
        return None


def _extract_json_ld_phones(html_text: str, default_country: str) -> list[tuple[str, str]]:
    """Extracts structured Schema.org `telephone` properties from `<script type="application/ld+json">`."""
    found: list[tuple[str, str]] = []
    for match in JSON_LD_SCRIPT_REGEX.finditer(html_text):
        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                tel = item.get("telephone")
                if isinstance(tel, str):
                    norm = normalize_phone_number(tel, default_country=default_country)
                    if norm:
                        found.append((norm, "SCHEMA_JSON_LD"))
                contact_points = item.get("contactPoint")
                if isinstance(contact_points, dict):
                    contact_points = [contact_points]
                if isinstance(contact_points, list):
                    for cp in contact_points:
                        if isinstance(cp, dict) and isinstance(cp.get("telephone"), str):
                            norm = normalize_phone_number(cp["telephone"], default_country=default_country)
                            if norm:
                                found.append((norm, "SCHEMA_JSON_LD"))
        except Exception:
            continue
    return found


def extract_phones_from_text(text: str, default_country: str = "UK") -> list[tuple[str, str]]:
    """
    Hybrid extraction waterfall:
    1. Schema.org JSON-LD `telephone` properties
    2. Explicit `<a href="tel:...">` anchor links
    3. Explicitly labeled numbers (`Tel:`, `Phone:`, `Switchboard:`)
    4. Google `libphonenumber` `PhoneNumberMatcher` scan over visible text
    """
    if not text:
        return []

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    # 1. Schema.org JSON-LD
    for norm, ptype in _extract_json_ld_phones(text, default_country):
        if norm not in seen:
            seen.add(norm)
            candidates.append((norm, ptype))

    # 2. Explicit <a href="tel:..."> links
    for match in TEL_HREF_REGEX.finditer(text):
        norm = normalize_phone_number(match.group(1), default_country=default_country)
        if norm and norm not in seen:
            seen.add(norm)
            candidates.append((norm, "PUBLISHED_TEL_LINK"))

    # 3. Explicitly labeled numbers
    for match in LABELED_PHONE_REGEX.finditer(text):
        norm = normalize_phone_number(match.group(1), default_country=default_country)
        if norm and norm not in seen:
            seen.add(norm)
            candidates.append((norm, "PUBLISHED_CONTACT_TEXT"))

    # 4. Strip scripts/styles/tags and run Google libphonenumber PhoneNumberMatcher
    visible_text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    visible_text = re.sub(r"<style\b[^>]*>.*?</style>", " ", visible_text, flags=re.I | re.S)
    visible_text = re.sub(r"<[^>]+>", " ", visible_text)

    iso_region = _resolve_iso_region(default_country)
    for region_try in (iso_region, "GB", "AU", "US"):
        try:
            for match in PhoneNumberMatcher(visible_text, region_try):
                if phonenumbers.is_valid_number(match.number):
                    norm = phonenumbers.format_number(match.number, PhoneNumberFormat.INTERNATIONAL)
                    if norm and norm not in seen:
                        seen.add(norm)
                        candidates.append((norm, "HQ_SWITCHBOARD"))
        except Exception:
            continue

    return candidates


def scrape_domain_contact_phone(domain: str, default_country: str = "UK") -> tuple[str | None, str]:
    """
    Scrapes the company's public website pages (/contact, /about, /privacy, /)
    for genuinely published phone numbers. Never fabricates a fallback.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    for path in ("/contact", "/contact-us", "/about", "/privacy", ""):
        url = f"https://{domain}{path}"
        try:
            with httpx.Client(timeout=4.0, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code == 200 and resp.text:
                    phones = extract_phones_from_text(resp.text, default_country=default_country)
                    if phones:
                        return phones[0]
        except Exception:
            continue
    return None, "NONE"


def resolve_company_phone(
    company: CompanyRecord,
    job_payloads: list[dict[str, Any]] | None = None,
    default_country: str = "UK",
    enable_dom_scrape: bool = True,
) -> tuple[str | None, str]:
    """
    Honest Phone Resolution Waterfall (Zero Fabrication):
    1. Extracts published phone numbers from ATS job descriptions / footers.
    2. Scrapes the company's own /contact, /about, /privacy pages for JSON-LD, `tel:` links, or valid numbers.
    3. Returns (None, 'NONE') if no real phone number is published.
    """
    if job_payloads:
        for payload in job_payloads:
            if not isinstance(payload, dict):
                continue
            text_chunks = [
                str(payload.get("description") or ""),
                str(payload.get("descriptionPlain") or ""),
                str(payload.get("content") or ""),
                str(payload.get("additional") or ""),
            ]
            combined_text = " ".join(t for t in text_chunks if t)
            phones = extract_phones_from_text(combined_text, default_country=default_country)
            if phones:
                company.phone_number = phones[0][0]
                company.phone_type = phones[0][1]
                return phones[0]

    if enable_dom_scrape and company.canonical_domain:
        phone, ptype = scrape_domain_contact_phone(
            company.canonical_domain,
            default_country=default_country,
        )
        if phone:
            company.phone_number = phone
            company.phone_type = ptype
            return phone, ptype

    company.phone_number = None
    company.phone_type = "NONE"
    return None, "NONE"
