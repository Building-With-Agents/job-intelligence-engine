# ruff: noqa: T201
"""One-shot cleanup: re-resolve ``zip_code`` on existing normalized_jobs — JIE #244.

Background
----------
Before PR #304 (this fix), the ``_resolve_zip_code`` lookup in
``normalization/mappers/jsearch.py`` truncated full state names to two
letters (``"Texas" → "TE"``), so the ``postal_geo_data`` lookup silently
failed every time JSearch sent a full state name. The promotion SQL also
omitted ``zip_code`` entirely, so even rows that had ``raw.zip_code`` set
in the source feed never propagated the value to ``job_postings``.

PR #304 fixes both gaps going forward (mapper handles full state names;
promotion threads ``zip_code`` through every SQL constant). This script
handles the **existing residual**: ``normalized_jobs`` rows where
``zip_code IS NULL`` but ``city`` and ``state_province`` are populated.
For each, we re-run the (now-fixed) resolver, ``UPDATE normalized_jobs``,
and propagate the result to any matching ``job_postings`` row.

Usage
-----
.. code-block:: bash

    # Dry run (default — counts what would be resolved, makes no writes)
    python scripts/backfill_zip_codes_normalized.py

    # Apply — writes resolved zip_code values to normalized_jobs and job_postings
    python scripts/backfill_zip_codes_normalized.py --apply

    # Limit per-run for staged backfills
    python scripts/backfill_zip_codes_normalized.py --apply --limit 500

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
from normalization.mappers.jsearch import _resolve_zip_code  # noqa: E402

log = structlog.get_logger()


_FETCH_CANDIDATES_SQL = text(
    """
    SELECT id, city, state_province
    FROM dbo.normalized_jobs
    WHERE zip_code IS NULL
      AND city IS NOT NULL
      AND state_province IS NOT NULL
    ORDER BY id ASC
    LIMIT :lim
    """
)

_UPDATE_NORMALIZED_ZIP_SQL = text("UPDATE dbo.normalized_jobs SET zip_code = :zip WHERE id = :nj_id")

# Propagate to job_postings via the source/external_id key. COALESCE preserves
# any zip_code that might have already been written there (defensive — should
# be NULL universally pre-fix, but this is idempotent under re-runs).
_UPDATE_JOB_POSTINGS_ZIP_SQL = text(
    """
    UPDATE dbo.job_postings
    SET zip_code = COALESCE(zip_code, :zip)
    WHERE source = (SELECT source FROM dbo.normalized_jobs WHERE id = :nj_id)
      AND external_id = (SELECT external_id FROM dbo.normalized_jobs WHERE id = :nj_id)
    """
)


def backfill(*, limit: int = 5000, apply: bool = False) -> dict[str, int]:
    """Re-resolve zip_code on candidate rows.

    Returns counts: ``{"candidates": N, "resolved": N, "unresolved": N, "failed": N}``.

    When ``apply=False`` (default), only counts and previews — no DB writes.
    """
    engine = get_engine()
    counts = {"candidates": 0, "resolved": 0, "unresolved": 0, "failed": 0}

    with engine.connect() as conn:
        rows = conn.execute(_FETCH_CANDIDATES_SQL, {"lim": limit}).fetchall()

    counts["candidates"] = len(rows)
    if not rows:
        log.info("zip_backfill_no_candidates", limit=limit)
        return counts

    log.info("zip_backfill_candidates_found", n=len(rows), apply=apply)

    if not apply:
        previewed = 0
        for nj_id, city, state in rows:
            zip_code = _resolve_zip_code(city, state, None)
            if zip_code:
                counts["resolved"] += 1
                if previewed < 10:
                    print(f"  [dry-run] id={nj_id} ({city}, {state}) -> {zip_code}")
                    previewed += 1
            else:
                counts["unresolved"] += 1
        if counts["resolved"] > 10:
            print(f"  [dry-run] ... and {counts['resolved'] - 10} more resolutions")
        return counts

    for nj_id, city, state in rows:
        try:
            zip_code = _resolve_zip_code(city, state, None)
            if not zip_code:
                counts["unresolved"] += 1
                log.info(
                    "zip_backfill_unresolved",
                    normalized_job_id=nj_id,
                    city=city,
                    state=state,
                )
                continue

            with session_scope() as session:
                session.execute(
                    _UPDATE_NORMALIZED_ZIP_SQL,
                    {"zip": zip_code, "nj_id": nj_id},
                )
                session.execute(
                    _UPDATE_JOB_POSTINGS_ZIP_SQL,
                    {"zip": zip_code, "nj_id": nj_id},
                )

            counts["resolved"] += 1
            log.info(
                "zip_backfill_resolved",
                normalized_job_id=nj_id,
                zip_code=zip_code,
            )
        except Exception as exc:
            counts["failed"] += 1
            log.warning(
                "zip_backfill_failed",
                normalized_job_id=nj_id,
                city=city,
                state=state,
                error=str(exc),
            )

    log.info("zip_backfill_complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot backfill: re-resolve zip_code on normalized_jobs (JIE #244). "
            "Default is dry-run; pass --apply to write. "
            "Export fixtures BEFORE --apply per JIE database protection rules."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write zip_code values (without this flag, runs in dry-run mode).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum rows to attempt per run. Default 5000 covers the full backlog in one pass.",
    )
    args = parser.parse_args()

    load_repo_root_dotenv()
    counts = backfill(limit=args.limit, apply=args.apply)

    print()
    print("=" * 60)
    print(f"Zip backfill summary ({'APPLY' if args.apply else 'DRY-RUN'}):")
    print(f"  candidates found : {counts['candidates']}")
    print(f"  resolved         : {counts['resolved']}")
    print(f"  unresolved       : {counts['unresolved']}")
    if args.apply:
        print(f"  failed           : {counts['failed']}")
    print("=" * 60)
    if args.apply and counts["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
