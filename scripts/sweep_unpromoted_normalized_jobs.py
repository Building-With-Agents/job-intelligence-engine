# ruff: noqa: T201
"""Sweep unpromoted normalized_jobs rows — JIE #289 defense in depth.

Background
----------
The live enrichment loop has guards (now loud at ERROR) that skip promotion
when ``normalized_job_id`` is missing from the enriched payload, when the
DB session fails mid-batch, or when the DB is unreachable. Without an audit
trail, those skipped rows used to disappear silently into ``normalized_jobs``
with no matching ``job_postings`` row — 469 such orphans accumulated by
2026-04-28.

This script reads ``normalized_jobs WHERE promoted_at IS NULL`` rows older
than the grace window (default 1 hour, set via ``--grace-minutes``), and
re-attempts promotion via the standard ``_insert_job_posting_from_normalized``
helper — bypassing the broken payload contract entirely. On success, it
stamps ``promoted_at = NOW()`` so the row is no longer in the sweep set.

The grace window avoids racing live ingestion (rows that were just inserted
and are about to be promoted by the live loop should not be touched here).

Usage
-----
.. code-block:: bash

    # Dry run (default — shows what would be promoted, makes no writes)
    python scripts/sweep_unpromoted_normalized_jobs.py

    # Promote up to 200 rows
    python scripts/sweep_unpromoted_normalized_jobs.py --apply

    # Tune the limit and grace window
    python scripts/sweep_unpromoted_normalized_jobs.py --apply --limit 500 --grace-minutes 30

Idempotent — safe to re-run. Reads ``PYTHON_DATABASE_URL`` from ``.env``.

Suitable for cron / GitHub Actions hourly schedule. The query uses an
indexed predicate on ``normalized_jobs.created_at WHERE promoted_at IS NULL``
(``ix_normalized_jobs_promoted_at_null``), so it stays cheap as the table grows.
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

_FETCH_UNPROMOTED_SQL = text(
    """
    SELECT id
    FROM dbo.normalized_jobs
    WHERE promoted_at IS NULL
      AND created_at < NOW() - (:grace_minutes || ' minutes')::interval
    ORDER BY created_at ASC
    LIMIT :lim
    """
)

_STAMP_PROMOTED_SQL = text("UPDATE dbo.normalized_jobs SET promoted_at = NOW() WHERE id = :nj_id")


def sweep(
    *,
    grace_minutes: int = 60,
    limit: int = 200,
    apply: bool = False,
) -> dict[str, int]:
    """Sweep up to ``limit`` unpromoted rows older than ``grace_minutes``.

    Returns a counts dict: ``{"candidates": N, "promoted": N, "already_present": N,
    "failed": N}``.

    When ``apply=False`` (default), only counts candidates and what would happen.
    """
    engine = get_engine()
    counts = {"candidates": 0, "promoted": 0, "already_present": 0, "failed": 0}

    with engine.connect() as conn:
        ids = [
            int(r[0])
            for r in conn.execute(
                _FETCH_UNPROMOTED_SQL,
                {"grace_minutes": str(grace_minutes), "lim": limit},
            ).fetchall()
        ]

    counts["candidates"] = len(ids)
    if not ids:
        log.info("sweep_no_candidates", grace_minutes=grace_minutes, limit=limit)
        return counts

    log.info("sweep_candidates_found", n=len(ids), grace_minutes=grace_minutes, apply=apply)

    if not apply:
        # Dry run — just report what would be touched
        for nj_id in ids[:10]:
            print(f"  [dry-run] would attempt promotion for normalized_job id={nj_id}")
        if len(ids) > 10:
            print(f"  [dry-run] ... and {len(ids) - 10} more")
        return counts

    for nj_id in ids:
        try:
            with session_scope() as session:
                # If a job_postings row already exists, it just needs the stamp.
                if resolve_job_posting_row(session, nj_id):
                    session.execute(_STAMP_PROMOTED_SQL, {"nj_id": nj_id})
                    counts["already_present"] += 1
                    log.info("sweep_already_present_stamped", normalized_job_id=nj_id)
                    continue

                resolved = _insert_job_posting_from_normalized(session, nj_id)
                if resolved is None:
                    counts["failed"] += 1
                    log.warning(
                        "sweep_insert_returned_none",
                        normalized_job_id=nj_id,
                        reason="likely missing required normalized_jobs fields",
                    )
                    continue

                session.execute(_STAMP_PROMOTED_SQL, {"nj_id": nj_id})
                counts["promoted"] += 1
                log.info(
                    "sweep_promoted",
                    normalized_job_id=nj_id,
                    job_posting_id=resolved.get("job_posting_id"),
                )
        except Exception as exc:
            counts["failed"] += 1
            log.warning("sweep_failed", normalized_job_id=nj_id, error=str(exc))

    log.info("sweep_complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep unpromoted normalized_jobs rows (JIE #289 defense). "
            "Default is dry-run; pass --apply to actually promote rows."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually promote rows (without this flag, runs in dry-run mode).",
    )
    parser.add_argument(
        "--grace-minutes",
        type=int,
        default=60,
        help="Skip rows newer than this many minutes (avoid racing live ingestion). Default: 60.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum rows to attempt per run. Default: 200.",
    )
    args = parser.parse_args()

    load_repo_root_dotenv()
    counts = sweep(grace_minutes=args.grace_minutes, limit=args.limit, apply=args.apply)

    print()
    print("=" * 60)
    print(f"Sweep summary ({'APPLY' if args.apply else 'DRY-RUN'}):")
    print(f"  candidates     : {counts['candidates']}")
    if args.apply:
        print(f"  promoted       : {counts['promoted']}")
        print(f"  already present: {counts['already_present']}")
        print(f"  failed         : {counts['failed']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
