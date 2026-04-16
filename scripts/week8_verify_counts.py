#!/usr/bin/env python3
"""Week 8 runbook — verify expected table counts across Weeks 6–8.

Prints row counts for every table the Week 8 pipeline depends on, grouped
by week. Gracefully reports MISSING for tables that don't exist yet
(useful when migrations haven't run locally).

Usage (from repo root with venv activated):

    python scripts/week8_verify_counts.py

Reads `PYTHON_DATABASE_URL` from the repo-root `.env`.
"""

from __future__ import annotations

from common.env import load_repo_root_dotenv

load_repo_root_dotenv()

from common.data_store.database import get_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402

# Section name -> list of (table_name, expected_rows_string)
_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Week 6 — Upstream pipeline",
        [
            ("raw_ingested_jobs", "1,568+"),
            ("job_ingestion_runs", "132+"),
            ("normalized_jobs", "651+"),
            ("normalization_quarantine", "16"),
            ("extracted_intelligence", "651+"),
            ("employer_profiles", "855+"),
            ("companies", "1,145+"),
            ("naics", "2,125"),
            ("job_postings", "1,736+"),
            ("llm_audit_log", "14,970+"),
        ],
    ),
    (
        "Week 7 — Analytics aggregates + clustering",
        [
            ("analytics_pipeline_state", "1+"),
            ("canonical_roles", "1+ (from clustering run)"),
            ("skill_demand_weekly", ">0 per week"),
            ("tool_demand_weekly", ">0 per week"),
            ("role_snapshot_weekly", ">0 per week"),
            ("sector_summary_weekly", ">0 per week"),
            ("geo_demand_weekly", ">0 per week"),
            ("skill_velocity", ">0 per week"),
            ("skill_co_occurrence", ">0 per week"),
            ("posting_freshness", ">0"),
            ("trajectory_map", ">=0 (Phase 2 scaffold)"),
        ],
    ),
    (
        "Week 8 — Q&A + disruption",
        [
            ("disruption_fingerprints", ">=1 after Pair A refresh"),
            ("orchestration_audit_log", ">=1 after Q&A calls (Pair D)"),
            ("cohort_gap_cache", ">=1 after trigger calls (Pair D)"),
        ],
    ),
]


def main() -> int:
    engine = get_engine()
    missing = 0
    empty = 0
    with engine.connect() as conn:
        for section, rows in _SECTIONS:
            print()
            print(f"=== {section} ===")
            print(f"{'table':30} {'count':>10}  expected")
            print("-" * 80)
            for tbl, expected in rows:
                try:
                    cnt = conn.execute(text(f"SELECT COUNT(*) FROM dbo.{tbl}")).scalar() or 0
                    marker = "" if cnt > 0 else "  (empty)"
                    if cnt == 0:
                        empty += 1
                    print(f"{tbl:30} {cnt:>10}  {expected}{marker}")
                except Exception:
                    missing += 1
                    print(f"{tbl:30} {'MISSING':>10}  {expected}")
    print()
    print(f"Summary: {missing} missing, {empty} empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
