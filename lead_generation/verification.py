from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import random
import re
import smtplib
import socket
import string
from typing import Protocol

from .models import (
    AttributionStatus,
    CompanyRecord,
    ContactObservation,
    IdentityConfidence,
    LeadGrade,
    RoutingStatus,
    SmtpStatus,
)


class VerificationStatus(str, Enum):
    NOT_CHECKED = "NOT_CHECKED"
    VERIFIED_PUBLIC = "VERIFIED_PUBLIC"
    ACCEPTING_SMTP = "ACCEPTING_SMTP"
    REJECTED_SMTP = "REJECTED_SMTP"
    CATCH_ALL_LIKELY = "CATCH_ALL_LIKELY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class VerificationResult:
    email: str
    status: VerificationStatus
    evidence: tuple[str, ...] = ()


class Verifier(Protocol):
    def verify(self, email: str) -> VerificationResult: ...


class SyntaxVerifier:
    def verify(self, email: str) -> VerificationResult:
        status = (
            VerificationStatus.VERIFIED_PUBLIC
            if "@" in email and " " not in email
            else VerificationStatus.UNKNOWN
        )
        return VerificationResult(email=email, status=status)


# ---------------------------------------------------------------------------
# Gate 5: Canary & SMTP Observation Engine (Port 25 + SEG + Tarpit Guards)
# ---------------------------------------------------------------------------

SEG_PROVIDERS = {"MIMECAST_SEG", "PROOFPOINT_SEG", "BARRACUDA_SEG"}

_port25_reachable_cache: bool | None = None


def check_port25_reachable(test_host: str = "aspmx.l.google.com", timeout: float = 2.5) -> bool:
    """
    Tests outbound TCP Port 25 connectivity once per worker lifecycle.
    Prevents hanging on cloud VMs (AWS/GCP/Azure/DigitalOcean) where ISP blocks Port 25.
    """
    global _port25_reachable_cache
    if _port25_reachable_cache is not None:
        return _port25_reachable_cache
    try:
        with socket.create_connection((test_host, 25), timeout=timeout):
            _port25_reachable_cache = True
    except OSError:
        _port25_reachable_cache = False
    return _port25_reachable_cache


def _extract_enhanced_code(resp_bytes: bytes | str) -> str | None:
    text = resp_bytes.decode("utf-8", errors="ignore") if isinstance(resp_bytes, bytes) else str(resp_bytes)
    match = re.search(r"\b([245]\.\d{1,3}\.\d{1,3})\b", text)
    return match.group(1) if match else None


def smtp_rcpt_probe(
    mx_host: str,
    target_email: str,
    helo_domain: str = "verify.local",
    sender_email: str = "probe@verify.local",
    timeout: float = 3.0,
) -> tuple[SmtpStatus, int | None, str | None]:
    """
    Executes a single non-intrusive SMTP RCPT TO handshake without sending DATA.
    Enforces a strict timeout (<= 3.0s) to defeat Microsoft 365 tarpitting.
    """
    try:
        with smtplib.SMTP(timeout=timeout) as server:
            server.connect(mx_host, 25)
            server.ehlo_or_helo_if_needed()
            mail_code, _ = server.mail(sender_email)
            if mail_code >= 400:
                return SmtpStatus.SMTP_TEMP_DEFERRED, mail_code, None
            rcpt_code, rcpt_msg = server.rcpt(target_email)
            enhanced = _extract_enhanced_code(rcpt_msg)
            try:
                server.quit()
            except Exception:
                pass

            if 200 <= rcpt_code < 300:
                return SmtpStatus.SMTP_RCPT_ACCEPTED, rcpt_code, enhanced
            if 500 <= rcpt_code < 600:
                return SmtpStatus.SMTP_RCPT_REJECTED, rcpt_code, enhanced
            return SmtpStatus.SMTP_TEMP_DEFERRED, rcpt_code, enhanced
    except (socket.timeout, TimeoutError, ConnectionRefusedError, OSError):
        return SmtpStatus.PROBE_BLOCKED_PORT25, None, None
    except smtplib.SMTPException:
        return SmtpStatus.SMTP_TEMP_DEFERRED, None, None


def verify_office365_https(target_email: str, timeout: float = 4.0) -> SmtpStatus:
    """
    Port-443 HTTPS Mailbox Verification for Microsoft 365 / Exchange Online domains
    (technique used by open-source verifiers `AfterShip/email-verifier` and `reacherhq`).
    Queries Microsoft's public `GetCredentialType` API over HTTPS (bypasses ISP Port 25 blocks):
    - IfExistsResult == 0, 5, 6 -> Mailbox exists (SMTP_RCPT_ACCEPTED)
    - IfExistsResult == 1       -> Mailbox does not exist (SMTP_RCPT_REJECTED)
    """
    import httpx

    url = "https://login.microsoftonline.com/common/GetCredentialType"
    payload = {
        "Username": target_email,
        "isOtherIdpSupported": True,
        "checkPhones": False,
        "isRemoteNGCSupported": True,
        "isCookieBannerShown": False,
        "isFidoSupported": True,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return SmtpStatus.PROBE_BLOCKED_PORT25
            data = resp.json()
            # ThrottleStatus == 1 means Microsoft rate-limited the probe
            if data.get("ThrottleStatus") == 1:
                return SmtpStatus.SMTP_TEMP_DEFERRED
            exists_code = data.get("IfExistsResult")
            if exists_code in (0, 5, 6):
                return SmtpStatus.SMTP_RCPT_ACCEPTED
            if exists_code == 1:
                return SmtpStatus.SMTP_RCPT_REJECTED
    except Exception:
        pass
    return SmtpStatus.PROBE_BLOCKED_PORT25


def probe_company_candidates(
    company: CompanyRecord,
    observations: list[ContactObservation],
    enable_live_smtp: bool = False,
    enable_https_o365_probe: bool = False,
) -> list[ContactObservation]:
    """
    Gate 5: Dual-Canary Catch-All Detection + Sequential Candidate SMTP/HTTPS Probing.
    1. If routing_status != MX_RESOLVED or no primary_mx_host -> skip probing.
    2. If inferred_provider is a known SEG (Mimecast/Proofpoint/Barracuda) -> PROBE_SKIPPED_SEG.
    3. If Port 25 is blocked and provider is MICROSOFT_365 (with enable_https_o365_probe=True),
       uses HTTPS Port 443 GetCredentialType canary + candidate verification.
    4. Otherwise, if live SMTP is disabled or Port 25 is blocked by cloud ISP -> PROBE_BLOCKED_PORT25.
    """
    if not observations:
        return []

    if company.routing_status != RoutingStatus.MX_RESOLVED or not company.primary_mx_host:
        return observations

    if company.inferred_provider in SEG_PROVIDERS:
        company.catch_all_status = "SEG_MASKED"
        return [
            ContactObservation(
                normalized_email=obs.normalized_email,
                discovery_tier=obs.discovery_tier,
                attribution_status=obs.attribution_status,
                source_url=obs.source_url,
                smtp_status=SmtpStatus.PROBE_SKIPPED_SEG,
                smtp_code=None,
                smtp_enhanced_code=None,
                observed_at=datetime.now(timezone.utc),
            )
            for obs in observations
        ]

    if not enable_live_smtp or not check_port25_reachable(company.primary_mx_host):
        if enable_https_o365_probe and company.inferred_provider == "MICROSOFT_365":
            rand_token = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            canary_status = verify_office365_https(f"zqx_probe_{rand_token}@{company.canonical_domain}")
            if canary_status == SmtpStatus.SMTP_RCPT_REJECTED:
                company.catch_all_status = "NEGATIVE_CANARY_CONFIRMED"
                probed_o365: list[ContactObservation] = []
                for obs in observations:
                    st = verify_office365_https(obs.normalized_email)
                    probed_o365.append(
                        ContactObservation(
                            normalized_email=obs.normalized_email,
                            discovery_tier=obs.discovery_tier,
                            attribution_status=obs.attribution_status,
                            source_url=obs.source_url,
                            smtp_status=st,
                            smtp_code=250 if st == SmtpStatus.SMTP_RCPT_ACCEPTED else (550 if st == SmtpStatus.SMTP_RCPT_REJECTED else None),
                            smtp_enhanced_code="2.1.5" if st == SmtpStatus.SMTP_RCPT_ACCEPTED else None,
                            observed_at=datetime.now(timezone.utc),
                        )
                    )
                    if st == SmtpStatus.SMTP_RCPT_ACCEPTED:
                        break
                return probed_o365

        return [
            ContactObservation(
                normalized_email=obs.normalized_email,
                discovery_tier=obs.discovery_tier,
                attribution_status=obs.attribution_status,
                source_url=obs.source_url,
                smtp_status=SmtpStatus.PROBE_BLOCKED_PORT25,
                smtp_code=None,
                smtp_enhanced_code=None,
                observed_at=datetime.now(timezone.utc),
            )
            for obs in observations
        ]

    # Dual-Canary Catch-All Probe
    mx_host = company.primary_mx_host
    domain = company.canonical_domain
    is_catch_all = False

    for _ in range(2):
        rand_token = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        canary_email = f"zqx_probe_{rand_token}@{domain}"
        c_status, c_code, c_enh = smtp_rcpt_probe(mx_host, canary_email)
        if c_status == SmtpStatus.PROBE_BLOCKED_PORT25:
            return [
                ContactObservation(
                    normalized_email=obs.normalized_email,
                    discovery_tier=obs.discovery_tier,
                    attribution_status=obs.attribution_status,
                    source_url=obs.source_url,
                    smtp_status=SmtpStatus.PROBE_BLOCKED_PORT25,
                    observed_at=datetime.now(timezone.utc),
                )
                for obs in observations
            ]
        if c_status == SmtpStatus.SMTP_RCPT_ACCEPTED:
            is_catch_all = True
            company.catch_all_status = "CATCH_ALL_SUSPECTED"
            break

    if is_catch_all:
        return [
            ContactObservation(
                normalized_email=obs.normalized_email,
                discovery_tier=obs.discovery_tier,
                attribution_status=obs.attribution_status,
                source_url=obs.source_url,
                smtp_status=SmtpStatus.CATCH_ALL_SUSPECTED,
                smtp_code=250,
                smtp_enhanced_code="2.1.5",
                observed_at=datetime.now(timezone.utc),
            )
            for obs in observations
        ]

    company.catch_all_status = "NEGATIVE_CANARY_CONFIRMED"
    probed: list[ContactObservation] = []
    for obs in observations:
        status, code, enh = smtp_rcpt_probe(mx_host, obs.normalized_email)
        probed.append(
            ContactObservation(
                normalized_email=obs.normalized_email,
                discovery_tier=obs.discovery_tier,
                attribution_status=obs.attribution_status,
                source_url=obs.source_url,
                smtp_status=status,
                smtp_code=code,
                smtp_enhanced_code=enh,
                observed_at=datetime.now(timezone.utc),
            )
        )
        # Stop probing further permutations once one candidate is explicitly accepted
        if status == SmtpStatus.SMTP_RCPT_ACCEPTED:
            break

    return probed


# ---------------------------------------------------------------------------
# Part 4: Deterministic 3-Axis Epistemic Lead Grader
# ---------------------------------------------------------------------------

def compute_lead_grade(
    routing_status: RoutingStatus,
    identity_confidence: IdentityConfidence,
    attribution_status: AttributionStatus,
    smtp_status: SmtpStatus,
) -> LeadGrade:
    """
    Evaluates the 3 independent evidence axes to assign the deliverable LeadGrade:
    - GRADE_A: Direct published evidence + MX_RESOLVED + not rejected by SMTP.
    - GRADE_B: Confirmed current executive + MX_RESOLVED + inferred/snippet email + SMTP_RCPT_ACCEPTED (negative canary).
    - GRADE_C: Confirmed current executive + MX_RESOLVED + inferred/snippet email + Catch-All / SEG / Port-25 Blocked.
    - REVIEW_QUEUE: Ambiguous executive identity, non-standard DNS routing (NO_MX_IMPLICIT_FALLBACK, CNAME, IPv6-only), or SMTP rejected.
    """
    if routing_status != RoutingStatus.MX_RESOLVED:
        return LeadGrade.REVIEW_QUEUE

    if identity_confidence != IdentityConfidence.CURRENT_CONFIRMED:
        return LeadGrade.REVIEW_QUEUE

    if smtp_status == SmtpStatus.SMTP_RCPT_REJECTED:
        return LeadGrade.REVIEW_QUEUE

    if attribution_status in (
        AttributionStatus.PUBLISHED_DIRECT_MATCH,
        AttributionStatus.PUBLISHED_PAGE_CONFIRMED,
    ):
        return LeadGrade.GRADE_A

    if attribution_status in (
        AttributionStatus.INFERRED_FORMAT_MATCH,
        AttributionStatus.INFERRED_CANDIDATE,
        AttributionStatus.SEARCH_SNIPPET_UNVALIDATED,
    ):
        if smtp_status == SmtpStatus.SMTP_RCPT_ACCEPTED:
            return LeadGrade.GRADE_B
        if smtp_status in (
            SmtpStatus.CATCH_ALL_SUSPECTED,
            SmtpStatus.PROBE_SKIPPED_SEG,
            SmtpStatus.PROBE_BLOCKED_PORT25,
            SmtpStatus.SMTP_TEMP_DEFERRED,
            SmtpStatus.UNPROBED,
        ):
            return LeadGrade.GRADE_C

    return LeadGrade.REVIEW_QUEUE


def select_best_observation(
    observations: list[ContactObservation],
) -> ContactObservation | None:
    """Selects the highest-confidence contact observation for a lead."""
    if not observations:
        return None

    for obs in observations:
        if obs.attribution_status in (
            AttributionStatus.PUBLISHED_DIRECT_MATCH,
            AttributionStatus.PUBLISHED_PAGE_CONFIRMED,
        ) and obs.smtp_status != SmtpStatus.SMTP_RCPT_REJECTED:
            return obs

    for obs in observations:
        if obs.smtp_status == SmtpStatus.SMTP_RCPT_ACCEPTED:
            return obs

    for obs in observations:
        if obs.smtp_status != SmtpStatus.SMTP_RCPT_REJECTED:
            return obs

    return observations[0]
