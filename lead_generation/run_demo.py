"""
Demonstration runner for the 7-Gate Deterministic Lead Generation Pipeline.
Executes Gates 0 through 6, logs intermediate gate transitions, and writes
the final leads to categorized outbox and CSV outputs.
"""
                                            
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lead_generation.exporting import CsvExporter, JsonLinesExporter
from lead_generation.models import CampaignCriteria, LeadGrade
from lead_generation.pipeline import LeadGenerationPipeline


def main():
    role = os.getenv("LEAD_ROLE", "Engineer")
    locations = ("UK", "Australia")
    quota = int(os.getenv("LEAD_QUOTA", "100"))

    print("=" * 80)
    print("🚀 DETERMINISTIC LEAD GENERATION PIPELINE — 7-GATE EXECUTION")
    print("=" * 80)
    print(f"Target Role:      {role}")
    print(f"Target Locations: {locations}")
    print(f"Target Quota:     {quota} (Hard Ceiling = 100)")
    print("-" * 80)

    criteria = CampaignCriteria(role=role, locations=locations, quota=quota)
    pipeline = LeadGenerationPipeline()

    print("\n[Gate 0 & 1] Ingesting ATS Job Signals & Balancing Regional Quotas...")
    result = pipeline.run_campaign(
        criteria,
        enable_dom_scrape=True,
        enable_web_mentions=True,
        enable_live_smtp=False,  # Cloud environments safely use Port 25 self-check guard
        persist_outbox=True,
    )

    boards = result["boards"]
    jobs = result["jobs"]
    leads = result["leads"]
    tabs = result["tabs"]

    print(f"  ✓ Discovered ATS Boards: {len(boards)}")
    for b in boards[:5]:
        print(f"    - [{b.platform.upper()}] {b.slug} -> {b.api_url}")
    if len(boards) > 5:
        print(f"    ... and {len(boards) - 5} more boards.")

    print(f"\n  ✓ Filtered Relevant Vacancies: {len(jobs)}")
    for j in jobs[:5]:
        print(f"    - {j.title} | {j.location} ({j.platform}:{j.board_slug})")

    print("\n" + "=" * 80)
    print("📋 PRODUCED LEADS & 3-AXIS EPISTEMIC EVIDENCE")
    print("=" * 80)

    for idx, lead in enumerate(leads, start=1):
        grade_badge = f"[{lead.lead_grade.value}]"
        print(f"\nLead #{idx}: {grade_badge} {lead.company.canonical_name} ({lead.company.canonical_domain})")
        print(f"  • Job Vacancy Signal:  {lead.job_title_signal} ({lead.location_bucket})")
        print(f"  • Job Posting URL:     {lead.job_url_signal}")
        print(f"  • Executive Identity:  {lead.exec_full_name} — {lead.exec_title}")
        print(f"    - LinkedIn (Must-Have): {lead.linkedin_url}")
        print(f"    - Phone (Sweet-Spot):   {lead.phone_number} [{lead.phone_type}]")
        print(f"    - Best Contact Email:   {lead.best_email}")
        print(f"  • 3-Axis Evidence Breakdown:")
        print(f"    - Axis 1 (DNS Route):    {lead.company.routing_status.value} ({lead.company.inferred_provider})")
        print(f"    - Axis 2 (Attribution):  {lead.attribution_status.value}")
        print(f"    - Axis 3 (Protocol Obs): {lead.smtp_status.value}")

    print("\n" + "=" * 80)
    print(f"📊 DELIVERABLE GRADES PARTITION SUMMARY (Total Leads: {len(leads)})")
    print("=" * 80)
    for grade_name, grade_leads in tabs.items():
        print(f"  • {grade_name:14}: {len(grade_leads)} leads")

    # Export to outputs/
    os.makedirs("outputs", exist_ok=True)
    csv_path = "outputs/leads_deliverable.csv"
    jsonl_path = "outputs/leads_deliverable.jsonl"

    CsvExporter(csv_path).export(leads)
    JsonLinesExporter(jsonl_path).export(leads)

    print(f"\n💾 Deliverables successfully exported:")
    print(f"  - CSV Output:   {csv_path}")
    print(f"  - JSONL Output: {jsonl_path}")
    print(f"  - Outbox DB:    lead_generation/.lead_outbox.db")
    print("=" * 80)


if __name__ == "__main__":
    main()
