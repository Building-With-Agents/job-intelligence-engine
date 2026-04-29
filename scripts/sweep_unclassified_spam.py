# ruff: noqa: T201
"""Periodic sweeper: classify ``dbo.job_postings`` rows missing a spam tier — JIE #308 Fix C.

Background
----------
Pre-JIE #308, `enrichment/job_postings_promotion.py` had three structural
defects on the spam classification pipeline:

1. None of `_UPDATE_CLEAN_SQL` / `_UPDATE_FLAGGED_SQL` / `_UPDATE_UNCERTAIN_SQL`
   wrote `spam_tier`, so the column stayed NULL on every promoted row.
2. `_UPDATE_UNCERTAIN_SQL` wrote neither `is_spam` nor `spam_score`, so
   uncertain rows were indistinguishable from "never classified."
3. PR #303's orphan-promotion path (`_insert_job_posting_from_normalized`)
   bypasses `apply_enrichment_to_job_postings` entirely, so orphans land in
   `job_postings` with all spam columns NULL.

The Fix B-1 SQL changes close defects #1 and #2 going forward. This sweeper
closes defect #3 and provides eventual consistency for any historical or
future row whose spam classification doesn't reach the live promotion path:
it finds rows where ``spam_tier IS NULL`` and runs the classifier on them.

Mirrors the pattern of ``scripts/sweep_unpromoted_normalized_jobs.py`` (PR
#301): default dry-run, a 1-hour grace window to avoid racing live promotion,
``--limit`` for staged runs, idempotent under re-runs.

Usage
-----
.. code-block:: bash

    # Dry run (default — counts candidates, makes no writes, no LLM calls)
    python scripts/sweep_unclassified_spam.py

    # Apply: classify and update up to 200 rows older than 1 hour
    python scripts/sweep_unclassified_spam.py --apply

    # Larger run with custom grace window
    python scripts/sweep_unclassified_spam.py --apply --limit 500 --grace-minutes 30

Idempotent — only touches rows where ``spam_tier IS NULL``. Reads
``PYTHON_DATABASE_URL`` from ``.env``.

**Important:** export fixtures before running with ``--apply``. Per the
JIE database protection rules (``~/.claude/CLAUDE.md``), ``job_postings``
mutations require a fixture snapshot first.

.. code-block:: bash

    python scripts/pg-seed-data/export_fixtures.py --scope all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from common.data_store.database import get_engine, session_scope  # noqa: E402
from common.env import load_repo_root_dotenv  # noqa: E402
from enrichment.classifiers.spam_preview import (  # noqa: E402
    apply_spam_tiers,
    score_spam_preview,
)
from enrichment.job_postings_promotion import _UPDATE_SPAM_ONLY_SQL  # noqa: E402

log = structlog.get_logger()


# Find rows that need classification: spam_tier NULL (the ground truth flag
# for "we never classified this"). Join out to extracted_intelligence so the
# classifier can read skills / tasks / responsibilities for context — many
# rows have NULL extraction, in which case the classifier degrades to
# "uncertain" gracefully.
_FETCH_CANDIDATES_SQL = text(
    """
    WITH unprocessed AS (
        SELECT DISTINCT ON (jp.job_posting_id)
               jp.job_posting_id,
               jp.job_title,
               jp.job_description,
               jp.createdat,
               ei.skills,
               ei.tools,
               ei.tasks,
               ei.responsibilities,
               ei.context,
               COALESCE(ei.extraction_failed, FALSE) AS extraction_failed
        FROM dbo.job_postings AS jp
        LEFT JOIN dbo.normalized_jobs AS nj
            ON nj.source = jp.source AND nj.external_id = jp.external_id
        LEFT JOIN dbo.extracted_intelligence AS ei
            ON ei.normalized_job_id = nj.id
        WHERE jp.spam_tier IS NULL
          AND jp.createdat < NOW() - make_interval(mins => :grace_minutes)
        ORDER BY jp.job_posting_id, ei.extracted_at DESC NULLS LAST
    )
    SELECT job_posting_id::text AS job_posting_id,
           job_title,
           job_description,
           skills,
           tools,
           tasks,
           responsibilities,
           context,
           extraction_failed
    FROM unprocessed
    ORDER BY createdat ASC
    LIMIT :lim
    """
)


def _extraction_dict(row: Any) -> tuple[dict[str, Any], bool, bool]:
    """Build the ``extraction`` dict the classifier expects + the failure flags."""
    extraction_failed = bool(row.get("extraction_failed"))
    extraction: dict[str, Any] = {}
    for key in ("skills", "tools", "tasks", "responsibilities", "context"):
        val = row.get(key)
        if val is not None:
            extraction[key] = val
    extraction_empty = not extraction and not extraction_failed
    return extraction, extraction_failed, extraction_empty


def sweep(*, limit: int = 200, grace_minutes: int = 60, apply: bool = False) -> dict[str, int]:
    """Classify up to ``limit`` rows where ``spam_tier IS NULL``.

    Returns counts: ``{"candidates": N, "classified_clean": N,
    "classified_flagged": N, "classified_uncertain": N, "failed": N}``.

    When ``apply=False`` (default), counts only — no LLM calls, no writes.
    """
    engine = get_engine()
    counts = {
        "candidates": 0,
        "classified_clean": 0,
        "classified_flagged": 0,
        "classified_uncertain": 0,
        "failed": 0,
    }

    with engine.connect() as conn:
        rows = (
            conn.execute(
                _FETCH_CANDIDATES_SQL,
                {"lim": limit, "grace_minutes": grace_minutes},
            )
            .mappings()
            .all()
        )

    counts["candidates"] = len(rows)
    if not rows:
        log.info("spam_sweep_no_candidates", limit=limit, grace_minutes=grace_minutes)
        return counts

    log.info(
        "spam_sweep_candidates_found",
        n=len(rows),
        apply=apply,
        grace_minutes=grace_minutes,
    )

    if not apply:
        for row in rows[:10]:
            print(f"  [dry-run] job_posting_id={row['job_posting_id']} title={(row.get('job_title') or '')[:60]!r}")
        if len(rows) > 10:
            print(f"  [dry-run] ... and {len(rows) - 10} more")
        return counts

    for row in rows:
        jp_id = row["job_posting_id"]
        try:
            extraction, extraction_failed, extraction_empty = _extraction_dict(dict(row))
            result = score_spam_preview(
                job_title=row.get("job_title") or "",
                job_description=row.get("job_description") or "",
                extraction=extraction,
                extraction_failed=extraction_failed,
                extraction_empty=extraction_empty,
            )

            # Map the classifier output to the strict 4-state model documented
            # in the JIE #308 plan: clean / flagged / uncertain / rejected.
            # Rejected rows are deliberately surfaced (is_spam=TRUE) here,
            # unlike the live promotion path that skips the UPDATE entirely —
            # the sweeper's role is to record what the classifier saw, even
            # for confirmed spam, so the column reflects ground truth.
            if result.spam_score is None:
                tier = "uncertain"
                is_spam: bool | None = None
                spam_score: float | None = None
            else:
                is_spam, tier = apply_spam_tiers(float(result.spam_score))
                spam_score = float(result.spam_score)

            with session_scope() as session:
                session.execute(
                    _UPDATE_SPAM_ONLY_SQL,
                    {
                        "job_posting_id": jp_id,
                        "is_spam": is_spam,
                        "spam_score": spam_score,
                        "spam_tier": tier,
                    },
                )

            if tier == "clean":
                counts["classified_clean"] += 1
            elif tier == "flagged":
                counts["classified_flagged"] += 1
            else:
                counts["classified_uncertain"] += 1
            log.info(
                "spam_sweep_classified",
                job_posting_id=jp_id,
                tier=tier,
                spam_score=spam_score,
            )
        except Exception as exc:
            counts["failed"] += 1
            log.warning(
                "spam_sweep_failed",
                job_posting_id=jp_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    log.info("spam_sweep_complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Periodic sweeper: classify job_postings rows missing spam_tier (JIE #308). "
            "Default is dry-run; pass --apply to run the classifier and write. "
            "Export fixtures BEFORE --apply per JIE database protection rules."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run the classifier and write results (without this flag, runs in dry-run mode).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum rows to classify per run. Default 200 — safe for hourly cron.",
    )
    parser.add_argument(
        "--grace-minutes",
        type=int,
        default=60,
        help="Skip rows newer than this many minutes — avoids racing live promotion.",
    )
    args = parser.parse_args()

    load_repo_root_dotenv()
    counts = sweep(limit=args.limit, grace_minutes=args.grace_minutes, apply=args.apply)

    print()
    print("=" * 60)
    print(f"Spam sweep summary ({'APPLY' if args.apply else 'DRY-RUN'}):")
    print(f"  candidates found       : {counts['candidates']}")
    if args.apply:
        print(f"  classified clean       : {counts['classified_clean']}")
        print(f"  classified flagged     : {counts['classified_flagged']}")
        print(f"  classified uncertain   : {counts['classified_uncertain']}")
        print(f"  failed                 : {counts['failed']}")
    print("=" * 60)
    if args.apply and counts["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
