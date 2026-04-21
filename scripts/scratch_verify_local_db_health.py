#!/usr/bin/env python3
"""Temporary local DB health check — DELETE THIS FILE when done.

Run from repo root (venv on):
    python scripts/scratch_verify_local_db_health.py

Reads PYTHON_DATABASE_URL from repo-root .env.
Exit code: 0 if no ERROR rows, 1 if any ERROR (WARN alone exits 0).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from common.data_store.database import get_engine
from common.env import load_repo_root_dotenv

load_repo_root_dotenv()

# Soft floors (post PR #198 / team note). Below these → WARN, not FAIL.
QNA_COLUMN_FLOORS = {
    "date_posted": 1_500,
    "seniority_level": 2_500,
    "is_remote": 1_500,
    "role_classification": 2_800,
    "salary_min": 400,
    "salary_max": 400,
    "salary_currency": 400,
    "salary_period": 400,
}

# Minimum row counts for core pipeline tables (below → WARN).
TABLE_COUNT_FLOORS = {
    "job_postings": 1_000,
    "normalized_jobs": 500,
    "extracted_intelligence": 500,
    "raw_ingested_jobs": 500,
    "employer_profiles": 500,
    "companies": 500,
}


def _emit(level: str, msg: str, lines: list[tuple[str, str]]) -> None:
    lines.append((level, msg))
    sym = {"OK": "[OK]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, level)
    print(f"  {sym} {msg}")


def main() -> int:
    print("=== scratch_verify_local_db_health (delete this script after use) ===\n")

    lines: list[tuple[str, str]] = []
    engine = get_engine()

    with engine.connect() as conn:
        # --- 1) Core table counts ---
        print("1) Core table row counts")
        for tbl, floor in TABLE_COUNT_FLOORS.items():
            try:
                n = conn.execute(text(f"SELECT COUNT(*) FROM dbo.{tbl}")).scalar() or 0
                if n < floor:
                    _emit("WARN", f"{tbl}: {n:,} rows (floor {floor:,})", lines)
                else:
                    _emit("OK", f"{tbl}: {n:,} rows", lines)
            except Exception as exc:
                _emit("ERROR", f"{tbl}: {exc}", lines)
        print()

        # --- 2) Q&A columns exist on job_postings ---
        print("2) Q&A columns present on dbo.job_postings")
        qna_cols = tuple(QNA_COLUMN_FLOORS.keys())
        try:
            in_list = ", ".join(f"'{c}'" for c in qna_cols)
            rows = conn.execute(
                text(
                    f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo' AND table_name = 'job_postings'
                      AND column_name IN ({in_list})
                    """
                )
            ).fetchall()
            found = {r[0] for r in rows}
            for c in qna_cols:
                if c not in found:
                    _emit("ERROR", f"missing column: {c}", lines)
                else:
                    _emit("OK", f"column exists: {c}", lines)
        except Exception as exc:
            _emit("ERROR", f"could not inspect job_postings columns: {exc}", lines)
        print()

        # --- 3) Non-null counts for Q&A columns ---
        print("3) Q&A column population (non-null counts)")
        try:
            row = conn.execute(
                text(
                    """
                    SELECT
                      COUNT(*) AS total_postings,
                      COUNT(date_posted) AS date_posted,
                      COUNT(seniority_level) AS seniority_level,
                      COUNT(is_remote) AS is_remote,
                      COUNT(role_classification) AS role_classification,
                      COUNT(salary_min) AS salary_min,
                      COUNT(salary_max) AS salary_max,
                      COUNT(salary_currency) AS salary_currency,
                      COUNT(salary_period) AS salary_period
                    FROM dbo.job_postings
                    """
                )
            ).mappings().one()
            total = row["total_postings"]
            print(f"     total job_postings: {total:,}")
            for col in qna_cols:
                n = row[col]
                floor = QNA_COLUMN_FLOORS[col]
                if n < floor:
                    _emit(
                        "WARN",
                        f"{col}: {n:,} non-null (floor {floor:,}) - run backfill if far below team ballpark",
                        lines,
                    )
                else:
                    _emit("OK", f"{col}: {n:,} non-null", lines)
        except Exception as exc:
            _emit("ERROR", f"Q&A count query failed: {exc}", lines)
        print()

        # --- 4) Schema drift checks ---
        print("4) Schema drift (common seed / migration issues)")
        try:
            r = conn.execute(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo' AND table_name = 'job_ingestion_runs'
                      AND column_name = 'id'
                    """
                )
            ).first()
            if r is None:
                _emit("WARN", "job_ingestion_runs.id: column not found", lines)
            elif str(r[0]).lower() in ("integer", "bigint", "smallint"):
                _emit(
                    "WARN",
                    "job_ingestion_runs.id is integer-like - fixtures expect UUID; "
                    "re-seed inserts may fail until table matches ORM",
                    lines,
                )
            elif str(r[0]).lower() == "uuid":
                _emit("OK", "job_ingestion_runs.id type is uuid", lines)
            else:
                _emit("WARN", f"job_ingestion_runs.id type is {r[0]!r}", lines)
        except Exception as exc:
            _emit("ERROR", f"job_ingestion_runs id check: {exc}", lines)

        try:
            r = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo' AND table_name = 'orchestration_audit_log'
                      AND column_name = 'event_type'
                    """
                )
            ).first()
            if r:
                _emit("OK", "orchestration_audit_log.event_type exists", lines)
            else:
                _emit(
                    "WARN",
                    "orchestration_audit_log.event_type missing - old table shape; "
                    "index migration may skip until column is added",
                    lines,
                )
        except Exception as exc:
            _emit(
                "WARN",
                f"orchestration_audit_log check skipped ({exc})",
                lines,
            )

    # --- Summary ---
    errors = [m for lvl, m in lines if lvl == "ERROR"]
    warns = [m for lvl, m in lines if lvl == "WARN"]
    print("--- Summary ---")
    print(f"  ERRORs: {len(errors)}")
    print(f"  WARNs:  {len(warns)}")
    if errors:
        print("\n  Fix ERRORs first (missing tables/columns/query failures).")
        return 1
    if warns:
        print("\n  WARNs are hints — often OK if you already have data; see messages above.")
    else:
        print("\n  No ERROR or WARN — looks good for Q&A / golden-question testing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
