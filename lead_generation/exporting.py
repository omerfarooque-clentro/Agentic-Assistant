from __future__ import annotations

from collections.abc import Iterable
import csv
from dataclasses import asdict
import json
import os
import sqlite3
import uuid
from typing import Any, Protocol

import httpx

from .models import ExportState, LeadGrade, LeadRecord, MAX_CAMPAIGN_QUOTA


class LeadExporter(Protocol):
    def export(self, records: Iterable[dict[str, object]]) -> None: ...


class JsonLinesExporter:
    def __init__(self, output_path: str) -> None:
        self.output_path = output_path

    def export(self, records: Iterable[dict[str, object]]) -> None:
        with open(self.output_path, "w", encoding="utf-8") as output:
            for idx, record in enumerate(records):
                if idx >= MAX_CAMPAIGN_QUOTA:
                    break
                output.write(json.dumps(record, default=str) + "\n")


class CsvExporter:
    HEADERS = (
        "lead_grade",
        "company_name",
        "canonical_domain",
        "location_bucket",
        "job_title_signal",
        "job_url_signal",
        "exec_full_name",
        "exec_title",
        "linkedin_url",
        "phone_number",
        "phone_type",
        "identity_confidence",
        "best_email",
        "attribution_status",
        "routing_status",
        "inferred_provider",
        "smtp_status",
        "identity_source_url",
    )

    def __init__(self, output_path: str) -> None:
        self.output_path = output_path

    def export(self, records: Iterable[LeadRecord | dict[str, Any]]) -> None:
        with open(self.output_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=self.HEADERS)
            writer.writeheader()
            for idx, rec in enumerate(records):
                if idx >= MAX_CAMPAIGN_QUOTA:
                    break
                row = lead_to_flat_dict(rec) if isinstance(rec, LeadRecord) else rec
                writer.writerow({k: row.get(k, "") for k in self.HEADERS})


def lead_to_flat_dict(lead: LeadRecord) -> dict[str, Any]:
    return {
        "campaign_role": lead.campaign_role,
        "location_bucket": lead.location_bucket,
        "company_name": lead.company.canonical_name,
        "canonical_domain": lead.company.canonical_domain,
        "routing_status": lead.company.routing_status.value,
        "primary_mx_host": lead.company.primary_mx_host,
        "inferred_provider": lead.company.inferred_provider,
        "job_title_signal": lead.job_title_signal,
        "job_url_signal": lead.job_url_signal,
        "vacancy_is_live": lead.vacancy_is_live,
        "exec_full_name": lead.exec_full_name,
        "normalized_exec_name": lead.normalized_exec_name,
        "exec_title": lead.exec_title,
        "linkedin_url": lead.linkedin_url or lead.identity_source_url,
        "phone_number": lead.phone_number or lead.company.phone_number,
        "phone_type": lead.phone_type or lead.company.phone_type,
        "identity_source_url": lead.identity_source_url,
        "identity_confidence": lead.identity_confidence.value,
        "best_email": lead.best_email,
        "attribution_status": lead.attribution_status.value,
        "smtp_status": lead.smtp_status.value,
        "lead_grade": lead.lead_grade.value,
        "export_state": lead.export_state.value,
        "export_batch_id": lead.export_batch_id,
    }


def partition_leads_by_grade(leads: Iterable[LeadRecord]) -> dict[str, list[dict[str, Any]]]:
    """Partitions leads into the 4 deliverable Google Sheets tabs: GRADE_A, GRADE_B, GRADE_C, REVIEW_QUEUE."""
    tabs: dict[str, list[dict[str, Any]]] = {
        LeadGrade.GRADE_A.value: [],
        LeadGrade.GRADE_B.value: [],
        LeadGrade.GRADE_C.value: [],
        LeadGrade.REVIEW_QUEUE.value: [],
    }
    for idx, lead in enumerate(leads):
        if idx >= MAX_CAMPAIGN_QUOTA:
            break
        grade_key = lead.lead_grade.value
        tabs.setdefault(grade_key, []).append(lead_to_flat_dict(lead))
    return tabs


# ---------------------------------------------------------------------------
# Gate 6: Persistent SQLite Transactional Outbox (Idempotent Crash-Safe Store)
# ---------------------------------------------------------------------------

OUTBOX_DB_PATH = os.path.join(os.path.dirname(__file__), ".lead_outbox.db")


class TransactionalOutbox:
    """
    Implements the Blueprint v4.1 normalized schema and Transactional Outbox Pattern:
    - Deduplicates ATS sources on (ats_platform, ats_slug)
    - Deduplicates Companies on (canonical_domain)
    - Deduplicates Leads on (campaign_role, canonical_domain, normalized_exec_name)
    - Atomically transitions export_state: PENDING -> IN_PROGRESS (with export_batch_id) -> EXPORTED
    """

    def __init__(self, db_path: str = OUTBOX_DB_PATH) -> None:
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS company_entities (
                    canonical_domain TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    routing_status TEXT NOT NULL,
                    primary_mx_host TEXT,
                    inferred_provider TEXT
                );

                CREATE TABLE IF NOT EXISTS company_ats_sources (
                    ats_platform TEXT NOT NULL,
                    ats_slug TEXT NOT NULL,
                    canonical_domain TEXT NOT NULL,
                    PRIMARY KEY (ats_platform, ats_slug)
                );

                CREATE TABLE IF NOT EXISTS lead_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_role TEXT NOT NULL,
                    location_bucket TEXT NOT NULL,
                    canonical_domain TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    job_title_signal TEXT NOT NULL,
                    job_url_signal TEXT NOT NULL,
                    exec_full_name TEXT NOT NULL,
                    normalized_exec_name TEXT NOT NULL,
                    exec_title TEXT NOT NULL,
                    linkedin_url TEXT,
                    phone_number TEXT,
                    phone_type TEXT,
                    identity_confidence TEXT NOT NULL,
                    best_email TEXT,
                    attribution_status TEXT NOT NULL,
                    routing_status TEXT NOT NULL,
                    smtp_status TEXT NOT NULL,
                    lead_grade TEXT NOT NULL,
                    export_state TEXT NOT NULL DEFAULT 'PENDING',
                    export_batch_id TEXT,
                    payload_json TEXT NOT NULL,
                    UNIQUE (campaign_role, canonical_domain, normalized_exec_name)
                );
                """
            )
            for col in ("linkedin_url", "phone_number", "phone_type"):
                try:
                    conn.execute(f"ALTER TABLE lead_outbox ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
        finally:
            conn.close()

    def save_lead_idempotent(self, lead: LeadRecord) -> bool:
        """
        Inserts or updates a LeadRecord in the transactional outbox without creating duplicates.
        Returns True if a new lead was inserted, False if it already existed.
        """
        flat = lead_to_flat_dict(lead)
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO company_entities
                (canonical_domain, canonical_name, routing_status, primary_mx_host, inferred_provider)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    lead.company.canonical_domain,
                    lead.company.canonical_name,
                    lead.company.routing_status.value,
                    lead.company.primary_mx_host,
                    lead.company.inferred_provider,
                ),
            )
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO lead_outbox (
                    campaign_role, location_bucket, canonical_domain, company_name,
                    job_title_signal, job_url_signal, exec_full_name, normalized_exec_name,
                    exec_title, linkedin_url, phone_number, phone_type, identity_confidence,
                    best_email, attribution_status, routing_status, smtp_status, lead_grade,
                    export_state, export_batch_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, ?)
                """,
                (
                    lead.campaign_role,
                    lead.location_bucket,
                    lead.company.canonical_domain,
                    lead.company.canonical_name,
                    lead.job_title_signal,
                    lead.job_url_signal,
                    lead.exec_full_name,
                    lead.normalized_exec_name,
                    lead.exec_title,
                    lead.linkedin_url or lead.identity_source_url,
                    lead.phone_number or lead.company.phone_number,
                    lead.phone_type or lead.company.phone_type,
                    lead.identity_confidence.value,
                    lead.best_email,
                    lead.attribution_status.value,
                    lead.company.routing_status.value,
                    lead.smtp_status.value,
                    lead.lead_grade.value,
                    json.dumps(flat, default=str),
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def claim_pending_batch(self, batch_limit: int = MAX_CAMPAIGN_QUOTA) -> tuple[str, list[dict[str, Any]]]:
        """
        Atomically claims up to `batch_limit` (max 100) PENDING rows with a unique `export_batch_id`.
        Prevents concurrent workers from double-exporting rows.
        """
        batch_id = str(uuid.uuid4())
        limit = max(1, min(batch_limit, MAX_CAMPAIGN_QUOTA))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, payload_json FROM lead_outbox WHERE export_state = 'PENDING' ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
            if not rows:
                conn.commit()
                return batch_id, []

            ids = [r[0] for r in rows]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE lead_outbox SET export_state = 'IN_PROGRESS', export_batch_id = ? WHERE id IN ({placeholders})",
                [batch_id, *ids],
            )
            conn.commit()

            records = []
            for _, payload_str in rows:
                item = json.loads(payload_str)
                item["export_state"] = ExportState.IN_PROGRESS.value
                item["export_batch_id"] = batch_id
                records.append(item)
            return batch_id, records
        finally:
            conn.close()

    def mark_batch_exported(self, batch_id: str) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE lead_outbox SET export_state = 'EXPORTED' WHERE export_batch_id = ?",
                (batch_id,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def requeue_failed_batch(self, batch_id: str) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE lead_outbox SET export_state = 'PENDING', export_batch_id = NULL WHERE export_batch_id = ?",
                (batch_id,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
