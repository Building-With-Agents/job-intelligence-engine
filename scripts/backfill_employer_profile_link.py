"""SQL-only linkback for ``job_postings.employer_profile_id``.

Many historical ``dbo.job_postings`` rows have ``company_id`` resolved and a
matching row in ``dbo.employer_profiles`` (created by prior enrichment runs)
but the ``employer_profile_id`` FK column is NULL. The live enrichment loop
now populates this on every new promotion (see issues #281 / #283 / PR #280),
but rows promoted before that fix landed remain unlinked.

This script does a single SQL UPDATE — no LLM calls, no Python loop — to
join ``job_postings`` against ``employer_profiles`` on ``company_id`` and
fill in the missing FK. Idempotent (only touches NULL rows).

Usage::

    python scripts/backfill_employer_profile_link.py
    python scripts/backfill_employer_profile_link.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
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


# Count rows that would be touched: jp has company_id but no profile linked,
# AND there's an employer_profile row for that company_id.
_PREVIEW_SQL = text(
    """
    SELECT
        COUNT(*) FILTER (WHERE jp.employer_profile_id IS NULL AND jp.company_id IS NOT NULL) AS unlinked_total,
        COUNT(*) FILTER (
            WHERE jp.employer_profile_id IS NULL
              AND jp.company_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM dbo.employer_profiles ep WHERE ep.company_id = jp.company_id)
        ) AS linkable
    FROM dbo.job_postings jp
    """
)

# Single UPDATE — Postgres updates the FK in place from the join.
_LINK_SQL = text(
    """
    UPDATE dbo.job_postings jp
    SET employer_profile_id = ep.id
    FROM dbo.employer_profiles ep
    WHERE jp.company_id = ep.company_id
      AND jp.employer_profile_id IS NULL
      AND jp.company_id IS NOT NULL
    """
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show counts only; do not UPDATE.")
    args = parser.parse_args()

    from common.data_store.database import session_scope

    with session_scope() as session:
        row = session.execute(_PREVIEW_SQL).mappings().one()
        unlinked_total = int(row["unlinked_total"])
        linkable = int(row["linkable"])

    print(f"job_postings rows with NULL employer_profile_id and non-null company_id: {unlinked_total}")
    print(f"  of which have a matching dbo.employer_profiles row:                    {linkable}")
    if unlinked_total > 0:
        print(f"  rows that will stay NULL (no matching profile to link):              {unlinked_total - linkable}")

    if args.dry_run:
        print("\n--dry-run: no changes made.")
        return 0

    if linkable == 0:
        print("\nNothing to link.")
        return 0

    log.info("backfill_employer_profile_link_start", linkable=linkable)
    with session_scope() as session:
        result = session.execute(_LINK_SQL)
        rowcount = result.rowcount
        log.info("backfill_employer_profile_link_done", rowcount=rowcount)

    print(f"\nLinked {rowcount} job_postings rows to employer_profiles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
