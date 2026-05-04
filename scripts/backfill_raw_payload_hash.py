# ruff: noqa: T201
"""Backfill ``raw_payload_hash`` on existing ``dbo.raw_ingested_jobs`` rows.

Why this script exists
----------------------
JIE#209 changed the hash function in [`ingestion/deduplicator.py`](../ingestion/deduplicator.py)
to drop ``date_posted`` from the hashed parts (so JSearch re-fetches with shifted
dates would dedup correctly). Rows ingested **before** that change still have the
old hash stored on disk.

The cross-batch dedup phase in ``deduplicate_batch`` does:

    SELECT raw_payload_hash FROM raw_ingested_jobs
     WHERE raw_payload_hash IN (newly_computed_hashes)

If a re-fetched job's NEW hash doesn't match any pre-existing OLD hash in the DB,
the row gets inserted as "new" and the table gains a duplicate. Observed
2026-05-04: a 1,535-row ingest produced 434 hash-drift duplicates against the
4,433 pre-existing rows — ~28% wastage.

This script recomputes ``raw_payload_hash`` for every row using the **current**
``compute_storage_hash`` function, so cross-batch dedup works correctly on the
next ingest.

Idempotent — a second ``--apply`` reports zero candidates (every row already
has the canonical hash).

Usage
-----
.. code-block:: bash

    # Dry-run (default — counts how many rows would change)
    python scripts/backfill_raw_payload_hash.py

    # Apply
    python scripts/backfill_raw_payload_hash.py --apply

Pre-requisite per CLAUDE.md DB protection rules: export fixtures before --apply.

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
from ingestion.deduplicator import compute_storage_hash  # noqa: E402

log = structlog.get_logger()


_SELECT_ALL_SQL = text(
    """
    SELECT id, source, external_id, title, company, raw_payload_hash AS old_hash
      FROM dbo.raw_ingested_jobs
    """
)

_UPDATE_SQL = text(
    """
    UPDATE dbo.raw_ingested_jobs
       SET raw_payload_hash = :new_hash
     WHERE id = :id
    """
)


def _row_record(row: Any) -> dict[str, Any]:
    return {
        "source": row.source,
        "external_id": row.external_id,
        "title": row.title or "",
        "company": row.company or "",
    }


def backfill(*, apply: bool = False, batch_size: int = 1000) -> dict[str, int]:
    engine = get_engine()
    counts = {"total": 0, "drift": 0, "in_sync": 0, "updated": 0, "hash_collision": 0}

    with engine.connect() as conn:
        rows = conn.execute(_SELECT_ALL_SQL).fetchall()

    counts["total"] = len(rows)
    log.info("backfill_scan", total=counts["total"])

    drift_rows: list[tuple[int, str, str]] = []  # (id, old_hash, new_hash)
    for row in rows:
        new_hash = compute_storage_hash(_row_record(row))
        if new_hash != row.old_hash:
            counts["drift"] += 1
            drift_rows.append((row.id, row.old_hash, new_hash))
        else:
            counts["in_sync"] += 1

    if not drift_rows:
        log.info("backfill_no_drift")
        return counts

    log.info("backfill_drift_found", drift=counts["drift"], in_sync=counts["in_sync"])

    if not apply:
        for rid, old, new in drift_rows[:5]:
            print(f"  [dry-run] id={rid} old_hash={old[:12]}... -> new_hash={new[:12]}...")
        if len(drift_rows) > 5:
            print(f"  [dry-run] ... and {len(drift_rows) - 5} more")
        return counts

    # Apply in batches inside a single transaction. ``raw_payload_hash`` has a
    # UNIQUE constraint (``uq_raw_ingested_jobs_hash``), so if recomputing
    # produces a collision (i.e., two rows now share the same logical hash), the
    # UPDATE fails. Track those separately — they're real Strategy B duplicates
    # that the JIE#209 cleanup script handles.
    with session_scope() as session:
        for i in range(0, len(drift_rows), batch_size):
            chunk = drift_rows[i : i + batch_size]
            for rid, _old, new_hash in chunk:
                try:
                    session.execute(_UPDATE_SQL, {"id": rid, "new_hash": new_hash})
                    counts["updated"] += 1
                except Exception as exc:
                    # UNIQUE violation = another row already has this hash =
                    # Strategy B duplicate. Leave the old hash alone for now;
                    # cleanup_jie209_dup_rows.py --apply will resolve it.
                    if "uq_raw_ingested_jobs_hash" in str(exc):
                        counts["hash_collision"] += 1
                        session.rollback()
                        # Need to re-open the transaction
                        continue
                    raise
            session.commit()
            log.info(
                "backfill_progress",
                processed=min(i + batch_size, len(drift_rows)),
                total=len(drift_rows),
                updated=counts["updated"],
                collisions=counts["hash_collision"],
            )

    log.info("backfill_complete", **counts)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute raw_payload_hash for all dbo.raw_ingested_jobs rows using the current "
            "compute_storage_hash function (JIE#209). Default is dry-run; pass --apply to mutate."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Actually update rows")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    load_repo_root_dotenv()
    counts = backfill(apply=args.apply, batch_size=args.batch_size)

    print()
    print("=" * 60)
    print(f"raw_payload_hash backfill ({'APPLY' if args.apply else 'DRY-RUN'}):")
    print(f"  total scanned   : {counts['total']:>6}")
    print(f"  in sync         : {counts['in_sync']:>6}")
    print(f"  drift detected  : {counts['drift']:>6}")
    if args.apply:
        print(f"  updated         : {counts['updated']:>6}")
        print(f"  hash collisions : {counts['hash_collision']:>6}  (pass to cleanup_jie209_dup_rows.py)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
