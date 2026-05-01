"""One-shot backfill: set NULL job_postings.quality_score from nj + extracted_intelligence.

JIE #328 — uses the same deterministic :func:`score_quality` path as
``enrichment.job_postings_promotion._derive_quality_from_normalized_job``.

Usage (repo root, venv, ``PYTHON_DATABASE_URL`` set)::

    python scripts/backfill_null_quality_job_postings.py --dry-run
    python scripts/backfill_null_quality_job_postings.py --limit 500

Refs: #328
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO / ".env")

from sqlalchemy import text  # noqa: E402

from common.data_store.database import session_scope  # noqa: E402
from enrichment.job_postings_promotion import _derive_quality_from_normalized_job  # noqa: E402

_LIST_NULL_QUALITY_SQL = text(
    """
    SELECT nj.id AS normalized_job_id, jp.job_posting_id::text AS job_posting_id
    FROM dbo.job_postings jp
    INNER JOIN dbo.normalized_jobs nj
        ON jp.source IS NOT NULL
        AND jp.external_id IS NOT NULL
        AND nj.source = jp.source
        AND nj.external_id = jp.external_id
    WHERE jp.quality_score IS NULL
        AND jp.company_id IS NOT NULL
        AND (jp.is_spam IS NOT TRUE)
    ORDER BY nj.id
    """
)

_UPDATE_QUALITY_SQL = text(
    """
    UPDATE dbo.job_postings
    SET quality_score = :quality_score
    WHERE job_posting_id::text = :job_posting_id
    """
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill NULL quality_score on job_postings (JIE #328).")
    parser.add_argument("--dry-run", action="store_true", help="Print counts only; no UPDATE.")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to update (0 = no limit).")
    args = parser.parse_args()

    if not os.getenv("PYTHON_DATABASE_URL"):
        print("PYTHON_DATABASE_URL is required.", file=sys.stderr)  # noqa: T201
        return 2

    with session_scope() as session:
        rows = session.execute(_LIST_NULL_QUALITY_SQL).mappings().all()
        total = len(rows)
        print(f"candidates_with_null_quality_score={total}")  # noqa: T201
        if args.dry_run or total == 0:
            return 0

        n_done = 0
        n_skipped = 0
        lim = args.limit if args.limit and args.limit > 0 else total
        for row in rows:
            if n_done >= lim:
                break
            nj_id = int(row["normalized_job_id"])
            jid = str(row["job_posting_id"])
            derived = _derive_quality_from_normalized_job(session, nj_id)
            if derived is None:
                n_skipped += 1
                continue
            qs, _components = derived
            session.execute(_UPDATE_QUALITY_SQL, {"quality_score": qs, "job_posting_id": jid})
            n_done += 1

        print(f"updated={n_done} skipped_no_nj_or_ei={n_skipped}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
