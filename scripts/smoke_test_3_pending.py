"""Siloed smoke test: re-stage 3 specific raw_ingested_jobs and run the full
pipeline (Norm → Extract → Enrich → promote to job_postings) on just those rows.

Verifies that the processing loop natively populates every field that the
backfill scripts (``backfill_qna_columns.py``, ``backfill_enrichment.py``)
were written to fill:

  * date_posted (typed timestamptz)
  * is_remote
  * salary_min / salary_max / salary_currency / salary_period
  * seniority_level
  * role_classification           (must NOT be 'N/A Not an IT role' for IT jobs)
  * quality_score
  * soc_code
  * naics_code
  * employer_profile_id

Usage::

    python scripts/smoke_test_3_pending.py             # full smoke run
    python scripts/smoke_test_3_pending.py --verify    # just check current state
    python scripts/smoke_test_3_pending.py --reset     # reset the 3 jobs (no run)

The 3 jobs are pinned by stable ``(source, external_id)`` so re-runs always
target the same records.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

from sqlalchemy import text  # noqa: E402

from common.data_store.database import get_engine  # noqa: E402

# The three jobs picked for smoke testing — short / medium / long descriptions
# from the 2026-04-22 ingestion batch. Pinned by (source, external_id) so the
# script is idempotent across worktree / branch / Postgres seed reseeds.
SMOKE_JOBS: list[tuple[str, str]] = [
    ("jsearch", "Rz65-nxVgZzfiGg8AAAAAA=="),  # OpenClaw AI Agent Developer (~1158 chars)
    ("jsearch", "UgUXS_BEMNmc9W2wAAAAAA=="),  # Scale-Secure AI Platform Engineer (~661 chars)
    ("jsearch", "ll_g6eB7Up-h6F8uAAAAAA=="),  # OpenClaw AI Agent Developer (~1158 chars)
]


def _reset_to_pending() -> None:
    """Delete derived rows for the 3 jobs and reset raw_ingested_jobs to pending.

    Idempotent. Bumps ``created_at`` so the records sort to FIFO-front and the
    next processing_loop pass picks them first.
    """
    engine = get_engine()
    with engine.begin() as conn:
        # Find raw_ids for the 3 jobs
        ids = (
            conn.execute(
                text(
                    "SELECT id FROM dbo.raw_ingested_jobs "
                    "WHERE (source, external_id) IN ("
                    + ",".join(f"(:s{i}, :e{i})" for i in range(len(SMOKE_JOBS)))
                    + ")"
                ),
                {f"s{i}": s for i, (s, _) in enumerate(SMOKE_JOBS)}
                | {f"e{i}": e for i, (_, e) in enumerate(SMOKE_JOBS)},
            )
            .scalars()
            .all()
        )
        if not ids:
            print("ERROR: no raw_ingested_jobs match the 3 (source, external_id) keys.")
            sys.exit(2)
        print(f"Found raw_ids: {ids}")

        # Delete derived rows (extracted_intelligence then normalized_jobs).
        # job_postings rows for these external_ids are deleted too so the
        # promotion path runs cleanly from scratch.
        conn.execute(
            text(
                "DELETE FROM dbo.extracted_intelligence WHERE normalized_job_id IN ("
                "    SELECT id FROM dbo.normalized_jobs "
                "    WHERE (source, external_id) IN ("
                + ",".join(f"(:s{i}, :e{i})" for i in range(len(SMOKE_JOBS)))
                + "))"
            ),
            {f"s{i}": s for i, (s, _) in enumerate(SMOKE_JOBS)} | {f"e{i}": e for i, (_, e) in enumerate(SMOKE_JOBS)},
        )
        conn.execute(
            text(
                "DELETE FROM dbo.normalized_jobs "
                "WHERE (source, external_id) IN (" + ",".join(f"(:s{i}, :e{i})" for i in range(len(SMOKE_JOBS))) + ")"
            ),
            {f"s{i}": s for i, (s, _) in enumerate(SMOKE_JOBS)} | {f"e{i}": e for i, (_, e) in enumerate(SMOKE_JOBS)},
        )
        conn.execute(
            text(
                "DELETE FROM dbo.job_postings "
                "WHERE (source, external_id) IN (" + ",".join(f"(:s{i}, :e{i})" for i in range(len(SMOKE_JOBS))) + ")"
            ),
            {f"s{i}": s for i, (s, _) in enumerate(SMOKE_JOBS)} | {f"e{i}": e for i, (_, e) in enumerate(SMOKE_JOBS)},
        )

        # Reset processing_status to pending and bump to FIFO-front
        conn.execute(
            text(
                "UPDATE dbo.raw_ingested_jobs SET "
                "  processing_status='pending', "
                "  error_message=NULL, "
                "  created_at = ("
                "    SELECT MIN(created_at) - INTERVAL '10 second' "
                "    FROM dbo.raw_ingested_jobs WHERE processing_status='pending'"
                "  ) + (id * INTERVAL '1 millisecond') "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
    print(f"Reset {len(ids)} jobs to pending (FIFO-front).")


def _run_processing_loop() -> int:
    """Subprocess the existing processing loop with batch-size=3, max-iterations=1."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        str(_REPO_ROOT / "scripts" / "run_processing_loop.py"),
        "--batch-size",
        "3",
        "--max-iterations",
        "1",
        "--delay",
        "0",
    ]
    print(f"\n>>> {' '.join(cmd)}\n")
    return subprocess.run(cmd, env=env, cwd=_REPO_ROOT).returncode


def _verify() -> bool:
    """Inspect the 9 backfill-equivalent fields and print PASS/FAIL per field."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    """
                SELECT n.id AS norm_id, n.source, n.external_id, n.title,
                       e.id AS extracted_id, e.extraction_failed, e.overall_confidence,
                       jp.job_posting_id,
                       jp.quality_score, jp.is_spam, jp.spam_tier,
                       jp.soc_code, jp.naics_code, jp.employer_profile_id,
                       jp.role_classification, jp.seniority_level,
                       jp.date_posted, jp.is_remote,
                       jp.salary_min, jp.salary_max, jp.salary_currency, jp.salary_period
                FROM dbo.normalized_jobs n
                LEFT JOIN dbo.extracted_intelligence e ON e.normalized_job_id = n.id
                LEFT JOIN dbo.job_postings jp
                  ON jp.source = n.source AND jp.external_id = n.external_id
                WHERE (n.source, n.external_id) IN (
                """
                    + ",".join(f"(:s{i}, :e{i})" for i in range(len(SMOKE_JOBS)))
                    + ") ORDER BY n.id"
                ),
                {f"s{i}": s for i, (s, _) in enumerate(SMOKE_JOBS)}
                | {f"e{i}": e for i, (_, e) in enumerate(SMOKE_JOBS)},
            )
            .mappings()
            .all()
        )

    if len(rows) != 3:
        print(f"FAIL: expected 3 rows, got {len(rows)}")
        return False

    all_ok = True
    print("\n=== VERIFICATION ===\n")
    for r in rows:
        print(f"--- norm_id={r['norm_id']} ext={r['external_id']!r} title={(r['title'] or '')[:55]!r} ---")
        # Stage gates
        print(
            f"  EXTRACT  : extracted_id={r['extracted_id']!s:6} failed={r['extraction_failed']!s:5} confidence={r['overall_confidence']}"
        )
        if r["extraction_failed"] is not False:
            print("    FAIL: extraction did not succeed")
            all_ok = False
        promoted = r["job_posting_id"] is not None
        print(f"  PROMOTE  : job_posting_id={r['job_posting_id']!s}")
        if not promoted:
            spam_tier = r.get("spam_tier")
            if spam_tier == "rejected":
                print(f"    INFO: rejected as spam (tier={spam_tier!r}); promotion intentionally skipped")
            else:
                print("    FAIL: not promoted to job_postings (and not spam-rejected)")
                all_ok = False
            print()
            continue

        # Field-by-field check vs the 9 backfill-equivalent columns
        checks = [
            ("quality_score", r["quality_score"] is not None),
            ("soc_code", r["soc_code"] is not None),
            ("naics_code", r["naics_code"] is not None),
            ("employer_profile_id", r["employer_profile_id"] is not None),
            ("date_posted", r["date_posted"] is not None),
            ("is_remote", r["is_remote"] is not None),
            ("seniority_level", r["seniority_level"] is not None),
            (
                "role_classification",
                r["role_classification"] is not None and r["role_classification"] != "N/A Not an IT role",
            ),
        ]
        salary_present = any(r[c] is not None for c in ("salary_min", "salary_max", "salary_currency", "salary_period"))
        checks.append(("salary_*", salary_present or "tolerated (source has no salary)"))
        for name, ok in checks:
            tag = "OK   " if ok is True else "TOLER" if isinstance(ok, str) else "FAIL "
            val_repr = ""
            if name == "role_classification":
                val_repr = f" -> {r['role_classification']!r}"
            elif name == "soc_code":
                val_repr = f" -> {r['soc_code']!r}"
            elif name == "naics_code":
                val_repr = f" -> {r['naics_code']!r}"
            elif name == "seniority_level":
                val_repr = f" -> {r['seniority_level']!r}"
            print(f"    [{tag}] {name}{val_repr}")
            if ok is False:
                all_ok = False
        print()

    print("=" * 50)
    print("OVERALL:", "PASS" if all_ok else "FAIL")
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="Only verify; do not reset or run.")
    parser.add_argument("--reset", action="store_true", help="Only reset; do not run or verify.")
    parser.add_argument("--no-reset", action="store_true", help="Run + verify but skip reset.")
    args = parser.parse_args()

    if args.verify:
        return 0 if _verify() else 1

    if not args.no_reset:
        _reset_to_pending()

    if args.reset:
        return 0

    rc = _run_processing_loop()
    if rc != 0:
        print(f"FAIL: processing_loop exited {rc}")
        return rc
    return 0 if _verify() else 1


if __name__ == "__main__":
    sys.exit(main())
