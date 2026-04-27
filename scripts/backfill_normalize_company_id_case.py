"""Pure-SQL fix for ``job_postings.company_id`` UUID-case mismatches.

Surfaced during PR #280's employer-profile backfill (issue #284): the
``dbo.companies.company_id`` column is **TEXT** (not ``uuid``), so string
equality is case-sensitive. Some ``job_postings.company_id`` values were
written in lowercase while ``dbo.companies`` stores the same UUID in
uppercase. The FK constraint sees a string mismatch and rejects them as
orphans — even though the company IS in the table, just under a
case-different stringification.

This script does a single SQL UPDATE — no LLM, no Python loop — that
copies the case-correct UUID string from ``dbo.companies`` into
``job_postings.company_id`` for every mismatched row. Idempotent.

Usage::

    python scripts/backfill_normalize_company_id_case.py --dry-run
    python scripts/backfill_normalize_company_id_case.py
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


_PREVIEW_SQL = text(
    """
    SELECT
        COUNT(*) FILTER (
            WHERE EXISTS (
                SELECT 1 FROM dbo.companies c
                WHERE UPPER(c.company_id) = UPPER(jp.company_id)
                  AND c.company_id <> jp.company_id
            )
        ) AS case_mismatched,
        COUNT(*) FILTER (
            WHERE NOT EXISTS (
                SELECT 1 FROM dbo.companies c
                WHERE UPPER(c.company_id) = UPPER(jp.company_id)
            )
        ) AS truly_missing
    FROM dbo.job_postings jp
    WHERE jp.company_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM dbo.companies cc WHERE cc.company_id = jp.company_id)
    """
)


_FIX_SQL = text(
    """
    UPDATE dbo.job_postings jp
    SET company_id = c.company_id
    FROM dbo.companies c
    WHERE UPPER(c.company_id) = UPPER(jp.company_id)
      AND c.company_id <> jp.company_id
    """
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show counts only; no UPDATE.")
    args = parser.parse_args()

    from common.data_store.database import session_scope

    with session_scope() as session:
        row = session.execute(_PREVIEW_SQL).mappings().one()
    case_mismatched = int(row["case_mismatched"])
    truly_missing = int(row["truly_missing"])

    print(f"Orphan-FK rows fixable by case-normalization: {case_mismatched}")
    print(f"Orphan-FK rows truly missing from companies:  {truly_missing}")

    if args.dry_run:
        print("\n--dry-run: no changes made.")
        return 0
    if case_mismatched == 0:
        print("\nNothing to fix.")
        return 0

    log.info("backfill_company_id_case_start", case_mismatched=case_mismatched)
    with session_scope() as session:
        result = session.execute(_FIX_SQL)
        rowcount = result.rowcount
        log.info("backfill_company_id_case_done", rowcount=rowcount)

    print(f"\nNormalized {rowcount} job_postings.company_id values to match dbo.companies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
