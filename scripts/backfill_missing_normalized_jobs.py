# ruff: noqa: T201
"""Backfill job_postings rows that have no matching normalized_jobs record — JIE #399.

Background
----------
790 rows exist in ``dbo.job_postings`` with no matching ``dbo.normalized_jobs``
record by ``(source, external_id)``. The clustering loader uses an INNER JOIN on
this key — these rows are invisible to clustering and permanently keep
``canonical_role_id = NULL``.

Root cause (confirmed): all 790 rows trace to a single 1-hour window on
2026-04-03 (61 ingestion runs, all "completed"). No gap rows exist from any later
date — the root cause is historical, not ongoing.

Population breakdown (as of 2026-05-23):
  - 330 recoverable: raw payload still exists in raw_ingested_jobs
  - 460 irrecoverable: raw payload is gone — documented but cannot be requeued

What this script does
---------------------
For recoverable rows: resets ``raw_ingested_jobs.processing_status`` to
``'pending'`` so the standard normalization → extraction → enrichment pipeline
picks them up on the next processing loop run.

For irrecoverable rows: stamps ``job_postings.ingestion_run_id`` with the
sentinel value ``'irrecoverable-backfill-399'`` so they are queryable as a
distinct population in the DB (not silently indistinguishable from rows awaiting
clustering). The original payload is gone and these rows cannot be requeued.

After running this script with ``--apply``, run the processing loop to complete
the pipeline for the recoverable rows::

    python scripts/run_processing_loop.py --batch-size 50 --delay 5

Then rerun clustering to pick up the newly created normalized_jobs records::

    python scripts/run_clustering.py

Usage
-----
    # Dry run (default — counts what would be reset, makes no writes)
    python scripts/backfill_missing_normalized_jobs.py

    # Apply — reset recoverable raw records to pending
    python scripts/backfill_missing_normalized_jobs.py --apply

    # Limit rows processed in one pass (useful for staged rollout)
    python scripts/backfill_missing_normalized_jobs.py --apply --limit 100

Idempotent — safe to re-run. Already-pending rows are counted but not double-reset.
Reads ``PYTHON_DATABASE_URL`` from ``.env``.

IMPORTANT: Export fixtures before running with ``--apply`` on the admin/Azure DB.
Per JIE database protection rules, ``raw_ingested_jobs`` and ``normalized_jobs``
are protected pipeline-audit tables::

    python scripts/pg-seed-data/export_fixtures.py --scope all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from common.data_store.database import get_engine, session_scope  # noqa: E402
from common.env import load_repo_root_dotenv  # noqa: E402

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_GAP_ROWS_SQL = text(
    """
    SELECT
        jp.job_posting_id::text AS posting_id,
        jp.source,
        jp.external_id,
        jp.ingestion_run_id,
        rij.id AS raw_id,
        rij.processing_status AS raw_status
    FROM dbo.job_postings jp
    LEFT JOIN dbo.raw_ingested_jobs rij
        ON rij.source = jp.source
        AND rij.external_id = jp.external_id
    WHERE jp.source IS NOT NULL
        AND jp.external_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM dbo.normalized_jobs nj
            WHERE nj.source = jp.source
            AND nj.external_id = jp.external_id
        )
    ORDER BY jp.job_posting_id
    LIMIT :lim
    """
)

_RESET_STATUS_SQL = text(
    """
    UPDATE dbo.raw_ingested_jobs
    SET processing_status = 'pending'
    WHERE id = :raw_id
    """
)

_STAMP_IRRECOVERABLE_SQL = text(
    """
    UPDATE dbo.job_postings
    SET ingestion_run_id = 'irrecoverable-backfill-399'
    WHERE job_posting_id = :posting_id::uuid
      AND ingestion_run_id != 'irrecoverable-backfill-399'
    """
)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def backfill(*, limit: int = 1000, apply: bool = False) -> dict[str, int]:
    """Audit gap rows and optionally reset recoverable ones to pending.

    Returns counts::

        {
            "total_gap": N,
            "recoverable": N,
            "irrecoverable": N,
            "reset": N,           # rows actually reset to pending (apply=True only)
            "already_pending": N,  # recoverable but already pending (no-op)
            "stamped": N,         # irrecoverable rows stamped in job_postings (apply=True only)
            "failed": N,          # write failures (reset or stamp); non-zero → exit code 1
        }
    """
    counts: dict[str, int] = {
        "total_gap": 0,
        "recoverable": 0,
        "irrecoverable": 0,
        "reset": 0,
        "already_pending": 0,
        "stamped": 0,
        "failed": 0,
    }

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(_GAP_ROWS_SQL, {"lim": limit}).mappings().all()

    rows = [dict(r) for r in rows]
    counts["total_gap"] = len(rows)

    recoverable = [r for r in rows if r["raw_id"] is not None]
    irrecoverable = [r for r in rows if r["raw_id"] is None]
    counts["recoverable"] = len(recoverable)
    counts["irrecoverable"] = len(irrecoverable)

    if not rows:
        log.info("backfill_no_gap_rows", limit=limit)
        return counts

    log.info(
        "backfill_gap_rows_found",
        total=counts["total_gap"],
        recoverable=counts["recoverable"],
        irrecoverable=counts["irrecoverable"],
        apply=apply,
    )

    # Report irrecoverable rows; stamp them in job_postings when applying
    if irrecoverable:
        print(f"\n  [IRRECOVERABLE — {len(irrecoverable)} rows]")
        print("  These job_postings rows have no raw payload and cannot be requeued.")
        print("  They will remain with canonical_role_id = NULL.")
        if apply:
            print("  Stamping ingestion_run_id = 'irrecoverable-backfill-399' so they are queryable.")
        for row in irrecoverable[:10]:
            print(f"    posting_id={row['posting_id']}  source={row['source']}  run={row['ingestion_run_id']}")
        if len(irrecoverable) > 10:
            print(f"    ... and {len(irrecoverable) - 10} more (full list in structlog output)")
        for row in irrecoverable:
            log.info(
                "backfill_irrecoverable",
                posting_id=row["posting_id"],
                source=row["source"],
                external_id=row["external_id"],
                ingestion_run_id=row["ingestion_run_id"],
            )
            if apply:
                try:
                    with session_scope() as session:
                        session.execute(_STAMP_IRRECOVERABLE_SQL, {"posting_id": row["posting_id"]})
                    counts["stamped"] += 1
                    log.info(
                        "backfill_irrecoverable_stamped",
                        posting_id=row["posting_id"],
                    )
                except Exception as exc:
                    counts["failed"] += 1
                    log.warning(
                        "backfill_stamp_failed",
                        posting_id=row["posting_id"],
                        error=str(exc),
                    )

    if not apply:
        print(f"\n  [DRY-RUN] Would reset {len(recoverable)} raw_ingested_jobs rows to pending.")
        for row in recoverable[:5]:
            print(
                f"    raw_id={row['raw_id']}  status={row['raw_status']}  "
                f"source={row['source']}  posting_id={row['posting_id']}"
            )
        if len(recoverable) > 5:
            print(f"    ... and {len(recoverable) - 5} more")
        return counts

    # Apply: reset recoverable rows to pending
    for row in recoverable:
        if row["raw_status"] == "pending":
            counts["already_pending"] += 1
            log.info(
                "backfill_already_pending",
                raw_id=row["raw_id"],
                posting_id=row["posting_id"],
            )
            continue

        try:
            with session_scope() as session:
                session.execute(_RESET_STATUS_SQL, {"raw_id": row["raw_id"]})
            counts["reset"] += 1
            log.info(
                "backfill_reset_to_pending",
                raw_id=row["raw_id"],
                posting_id=row["posting_id"],
                previous_status=row["raw_status"],
            )
        except Exception as exc:
            counts["failed"] += 1
            log.warning(
                "backfill_reset_failed",
                raw_id=row["raw_id"],
                posting_id=row["posting_id"],
                error=str(exc),
            )

    log.info("backfill_complete", **counts)
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill 790 job_postings rows missing normalized_jobs records (JIE #399). "
            "Default is dry-run; pass --apply to reset recoverable rows to pending. "
            "Export fixtures BEFORE --apply per JIE database protection rules."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Reset recoverable raw_ingested_jobs rows to pending (without this flag, dry-run only).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Max gap rows to process per run. Default 1000 covers all 790 in one pass.",
    )
    args = parser.parse_args()

    load_repo_root_dotenv()

    print()
    print("=" * 70)
    print("BACKFILL: job_postings rows missing normalized_jobs (JIE #399)")
    print("=" * 70)

    counts = backfill(limit=args.limit, apply=args.apply)

    print()
    print("=" * 70)
    print(f"Summary ({'APPLY' if args.apply else 'DRY-RUN'}):")
    print(f"  Total gap rows found : {counts['total_gap']}")
    print(f"  Recoverable          : {counts['recoverable']}  (raw payload exists)")
    print(f"  Irrecoverable        : {counts['irrecoverable']}  (raw payload gone — documented above)")
    if args.apply:
        print(f"  Reset to pending     : {counts['reset']}")
        print(f"  Already pending      : {counts['already_pending']}  (no-op)")
        print(f"  Stamped irrecoverable: {counts['stamped']}")
        print(f"  Failed               : {counts['failed']}")
    print("=" * 70)

    if args.apply and counts["reset"] > 0:
        print()
        print("Next steps:")
        print("  1. Run the processing loop to normalize + extract + enrich the reset rows:")
        print("       python scripts/run_processing_loop.py --batch-size 50 --delay 5")
        print("  2. Rerun clustering to assign canonical_role_id to the newly created rows:")
        print("       python scripts/run_clustering.py")
        print("  3. Export fixtures so the fix is captured for everyone:")
        print("       python scripts/pg-seed-data/export_fixtures.py --scope all")

    if args.apply and counts["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
