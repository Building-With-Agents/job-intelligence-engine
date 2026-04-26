"""LLM backfill for ``job_postings`` rows where the employer_profile is missing.

Targets the residual rows that ``backfill_employer_profile_link.py`` could not
fix: rows with ``employer_profile_id IS NULL`` AND no matching row in
``dbo.employer_profiles`` for their ``company_id``. These rows pre-date the
fix in PR #280 (commit 000f3c7) where the live promotion path now generates
the employer_profile on every new promotion. To bring historical rows to
parity, this script runs ONLY the employer classifier (cheap LLM call —
not the full SOC + NAICS + quality re-enrichment that
``backfill_enrichment.py`` does) on each, upserts the profile, and links
the FK.

Idempotent: only processes rows whose ``employer_profile_id`` is currently
NULL. Safe to re-run if interrupted.

Usage::

    python scripts/backfill_employer_profile_create.py --dry-run
    python scripts/backfill_employer_profile_create.py
    python scripts/backfill_employer_profile_create.py --batch-size 25 --delay 2
    python scripts/backfill_employer_profile_create.py --max-records 10
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

import structlog  # noqa: E402
from sqlalchemy import text  # noqa: E402

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger()

# Find rows that need a fresh employer_profile generated. We pull title +
# description for the classifier prompt, plus company_name (joined from
# dbo.companies via company_id) for the build_employer_profile() signature.
_SELECT_PENDING_SQL = text(
    """
    SELECT
        jp.job_posting_id::text       AS job_posting_id,
        jp.company_id::text           AS company_id,
        jp.job_title                  AS title,
        jp.job_description            AS description,
        c.company_name                AS company_name
    FROM dbo.job_postings jp
    LEFT JOIN dbo.companies c ON c.company_id = jp.company_id
    WHERE jp.employer_profile_id IS NULL
      AND jp.company_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM dbo.employer_profiles ep WHERE ep.company_id = jp.company_id
      )
    ORDER BY jp.job_posting_id
    LIMIT :batch_size
    """
)

_LINK_SQL = text(
    """
    UPDATE dbo.job_postings
    SET employer_profile_id = CAST(:employer_profile_id AS uuid)
    WHERE job_posting_id::text = :job_posting_id
    """
)

_COUNT_SQL = text(
    """
    SELECT COUNT(*)
    FROM dbo.job_postings jp
    WHERE jp.employer_profile_id IS NULL
      AND jp.company_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM dbo.employer_profiles ep WHERE ep.company_id = jp.company_id
      )
    """
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate employer_profiles for residual unlinked job_postings rows.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--batch-size", type=int, default=25, help="Records per batch (default 25).")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between records (LLM rate limit, default 2).")
    parser.add_argument("--max-records", type=int, default=0, help="Cap total records processed (0 = all).")
    parser.add_argument("--dry-run", action="store_true", help="Show count only; no LLM calls, no UPDATEs.")
    args = parser.parse_args()

    from common.data_store.database import session_scope
    from enrichment.classifiers.employer_classifier import build_employer_profile
    from enrichment.employer_profile_storage import upsert_employer_profile_by_company_id

    with session_scope() as s:
        total = int(s.execute(_COUNT_SQL).scalar_one())
    print(f"Pending: {total} job_postings rows need employer_profile generation.")

    if args.dry_run:
        print("--dry-run: no LLM calls, no UPDATEs.")
        return 0
    if total == 0:
        print("Nothing to do.")
        return 0

    processed = 0
    succeeded = 0
    errors = 0

    while True:
        if args.max_records and processed >= args.max_records:
            break

        with session_scope() as session:
            rows = session.execute(_SELECT_PENDING_SQL, {"batch_size": args.batch_size}).mappings().all()
            if not rows:
                break

            for row in rows:
                if args.max_records and processed >= args.max_records:
                    break

                jp_id = row["job_posting_id"]
                cid = row["company_id"]
                title = row["title"] or ""
                desc = row["description"] or ""
                company_name = (row["company_name"] or "").strip() or "Unknown"

                try:
                    # 1. LLM classifier — same one the live enrichment path uses.
                    ep = build_employer_profile(desc, company_name, session)
                    # 2. Upsert into dbo.employer_profiles (mirrors the promotion path).
                    profile_id = upsert_employer_profile_by_company_id(
                        session,
                        cid,
                        ep.model_dump(mode="json"),
                    )
                    # 3. Link the FK on the job_posting row.
                    session.execute(
                        _LINK_SQL,
                        {"job_posting_id": jp_id, "employer_profile_id": str(profile_id)},
                    )
                    succeeded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "backfill_employer_profile_create_failed",
                        job_posting_id=jp_id,
                        company_id=cid,
                        company_name=company_name,
                        title=(title or "")[:60],
                        error=str(exc),
                    )
                    errors += 1

                processed += 1
                if processed % 10 == 0:
                    log.info(
                        "backfill_employer_profile_create_progress",
                        processed=processed,
                        succeeded=succeeded,
                        errors=errors,
                        remaining=total - processed,
                    )
                if args.delay > 0:
                    time.sleep(args.delay)

    log.info(
        "backfill_employer_profile_create_complete",
        processed=processed,
        succeeded=succeeded,
        errors=errors,
    )
    print()
    print("Backfill complete.")
    print(f"  Processed: {processed}")
    print(f"  Succeeded: {succeeded}")
    print(f"  Errors:    {errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
