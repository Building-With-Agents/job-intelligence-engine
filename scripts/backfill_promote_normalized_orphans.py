# ruff: noqa: T201
"""One-shot cleanup: promote the 469 normalized_jobs orphans — JIE #289.

Background
----------
PR #301 fixed the structural defect in the live ingestion → enrichment →
promotion loop that silently skipped promotion when ``normalized_job_id``
was missing from the enriched payload. New orphans should no longer
accumulate from now on, and the hourly sweeper at
``scripts/sweep_unpromoted_normalized_jobs.py`` will catch any that slip.

This script handles the **existing residual** — 469 orphan rows in
``normalized_jobs`` (Apr 8 / 12 / 15 / 26 vintages) that have no matching
``job_postings`` row via ``source`` / ``external_id``. Phase 1 diagnostic
(2026-04-28) confirmed these are **truly missing** rows, not casing
mismatches: a case-insensitive + trim JOIN returns the same 469 count,
so the rows were never inserted into ``job_postings`` at all.

The backfill calls the existing ``_insert_job_posting_from_normalized``
helper for each orphan (the same code path live promotion uses on a
miss-on-resolve), then stamps ``promoted_at`` so the row exits the
sweeper's candidate set.

Usage
-----
.. code-block:: bash

    # Dry run (default — counts what would be promoted, makes no writes)
    python scripts/backfill_promote_normalized_orphans.py

    # Promote all orphans
    python scripts/backfill_promote_normalized_orphans.py --apply

    # Limit a single run (sweeper-style, useful if you want to spread the load)
    python scripts/backfill_promote_normalized_orphans.py --apply --limit 50

Idempotent — safe to re-run. Reads ``PYTHON_DATABASE_URL`` from ``.env``.

**Important:** export fixtures before running with ``--apply``. Per the
JIE database protection rules (``~/.claude/CLAUDE.md``), ``normalized_jobs``
and ``job_postings`` are protected pipeline-audit tables on Gary's
source-of-truth database.

.. code-block:: bash

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
from enrichment.job_postings_promotion import (  # noqa: E402
    _insert_job_posting_from_normalized,
    resolve_job_posting_row,
)

log = structlog.get_logger()

# Find orphan rows: normalized_jobs WHERE no matching job_postings AND
# not yet promoted. Pre-#301 rows have promoted_at IS NULL universally
# (the column was just added), so this query naturally picks them up.
_FETCH_ORPHANS_SQL = text(
    """
    SELECT nj.id
    FROM dbo.normalized_jobs nj
    LEFT JOIN dbo.job_postings jp
        ON jp.source = nj.source AND jp.external_id = nj.external_id
    WHERE jp.job_posting_id IS NULL
      AND nj.promoted_at IS NULL
    ORDER BY nj.created_at ASC
    LIMIT :lim
    """
)

_STAMP_PROMOTED_SQL = text("UPDATE dbo.normalized_jobs SET promoted_at = NOW() WHERE id = :nj_id")


def backfill(*, limit: int = 1000, apply: bool = False) -> dict[str, int]:
    """Promote up to ``limit`` orphan normalized_jobs rows.

    Returns counts: ``{"orphans": N, "promoted": N, "already_present": N, "failed": N}``.

    When ``apply=False`` (default), only counts orphans and previews.
    """
    engine = get_engine()
    counts = {"orphans": 0, "promoted": 0, "already_present": 0, "failed": 0}

    with engine.connect() as conn:
        ids = [int(r[0]) for r in conn.execute(_FETCH_ORPHANS_SQL, {"lim": limit}).fetchall()]

    counts["orphans"] = len(ids)
    if not ids:
        log.info("backfill_no_orphans", limit=limit)
        return counts

    log.info("backfill_orphans_found", n=len(ids), apply=apply)

    if not apply:
        for nj_id in ids[:10]:
            print(f"  [dry-run] would attempt promotion for normalized_job id={nj_id}")
        if len(ids) > 10:
            print(f"  [dry-run] ... and {len(ids) - 10} more")
        return counts

    for nj_id in ids:
        try:
            with session_scope() as session:
                # Idempotent guard: if a job_postings row was created since we
                # snapshot the orphan list (e.g., live ingestion re-promoted it,
                # or the sweeper got there first), just stamp and continue.
                if resolve_job_posting_row(session, nj_id):
                    session.execute(_STAMP_PROMOTED_SQL, {"nj_id": nj_id})
                    counts["already_present"] += 1
                    log.info("backfill_already_present_stamped", normalized_job_id=nj_id)
                    continue

                resolved = _insert_job_posting_from_normalized(session, nj_id)
                if resolved is None:
                    counts["failed"] += 1
                    log.warning(
                        "backfill_insert_returned_none",
                        normalized_job_id=nj_id,
                        reason="missing required normalized_jobs fields (e.g., title/company)",
                    )
                    continue

                session.execute(_STAMP_PROMOTED_SQL, {"nj_id": nj_id})
                counts["promoted"] += 1
                log.info(
                    "backfill_promoted",
                    normalized_job_id=nj_id,
                    job_posting_id=resolved.get("job_posting_id"),
                )
        except Exception as exc:
            counts["failed"] += 1
            log.warning("backfill_failed", normalized_job_id=nj_id, error=str(exc))

    log.info("backfill_complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot backfill: promote the 469 normalized_jobs orphans (JIE #289). "
            "Default is dry-run; pass --apply to actually promote rows. "
            "Export fixtures BEFORE --apply per JIE database protection rules."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually promote rows (without this flag, runs in dry-run mode).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum rows to attempt per run. Default 1000 covers the full 469 in one pass.",
    )
    args = parser.parse_args()

    load_repo_root_dotenv()
    counts = backfill(limit=args.limit, apply=args.apply)

    print()
    print("=" * 60)
    print(f"Backfill summary ({'APPLY' if args.apply else 'DRY-RUN'}):")
    print(f"  orphans found  : {counts['orphans']}")
    if args.apply:
        print(f"  promoted       : {counts['promoted']}")
        print(f"  already present: {counts['already_present']}")
        print(f"  failed         : {counts['failed']}")
    print("=" * 60)
    if args.apply and counts["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
