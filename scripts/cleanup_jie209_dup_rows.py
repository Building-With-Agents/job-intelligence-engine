# ruff: noqa: T201
"""One-shot cleanup: remove bug-introduced duplicates from JIE pipeline tables (JIE #209).

Background
----------
[`ingestion/deduplicator.py`](../ingestion/deduplicator.py) computed the storage
hash from ``[source, external_id, title, company, date_posted]``. JSearch
mutates ``date_posted`` between fetches for the same job (the upstream
``job_posted_at_datetime_utc`` drifts as the posting is refreshed / ages),
so the same ``(source, external_id)`` re-ingested with a shifted date
produces a *different* ``raw_payload_hash``, bypassing the
``uq_raw_ingested_jobs_hash`` unique constraint.

Result on Gary's source-of-truth Postgres on 2026-05-03:

* ``raw_ingested_jobs`` (jsearch): 5,259 rows, 4,373 distinct ``(source, external_id)``,
  **886 excess rows** across ~862 dup groups (one external_id had 7 copies).
* ``normalized_jobs`` (affected pairs): 1,712 rows, 861 distinct pairs
  → ~851 excess.
* ``extracted_intelligence`` (linked to affected ``normalized_jobs``): 1,712
  → ~851 excess.
* ``job_postings`` (affected pairs): 862 rows, 862 distinct pairs → **0 excess**.
  The ``job_postings`` upsert is correctly keyed on ``(source, external_id)``;
  the bug only inflated the staging tables, not the canonical postings.
* ``llm_audit_log``: not touched. Records real LLM spend; preserved unconditionally.

These extras skew analytics — a single posting duplicated 2-7× weights its
skills/employer/region in every aggregate. Per Gary 2026-05-03, this is
*bug-introduced garbage* and a deliberate one-time exception to the standing
"no DELETE on pipeline tables" rule (CLAUDE.md #9 / ``feedback_raw_ingested_jobs_immutable.md``).

Strategy
--------
For each ``(source, external_id)`` group with more than one row in a given
table, keep the row with the **earliest** timestamp (and lowest ``id`` as
tie-breaker) and remove the others.

* ``raw_ingested_jobs``: rank by (``ingestion_timestamp`` ASC, ``id`` ASC).
* ``normalized_jobs``: rank by (``created_at`` ASC, ``id`` ASC).
* ``extracted_intelligence``: cascade — drop rows whose ``normalized_job_id``
  is in the excess ``normalized_jobs`` set.

All deletes run inside a single transaction. Rolls back on any error.
``llm_audit_log`` and ``job_postings`` are not touched.

Usage
-----
.. code-block:: bash

    # Dry run (default — counts what would be removed, makes no writes)
    python scripts/cleanup_jie209_dup_rows.py

    # Apply the cleanup
    python scripts/cleanup_jie209_dup_rows.py --apply

Pre-requisites (per Gary 2026-05-03):

1. ``pg_dump`` backup at ``C:/Users/garyl/backups/jie/jie-sot-pre-jie209-cleanup-2026-05-03.dump``.
2. Fixture export at ``scripts/pg-seed-data/fixtures/`` (run ``export_fixtures.py --scope all``).

Idempotent — a second ``--apply`` reports zero candidates.
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

log = structlog.get_logger()


# Strategy B (per Gary 2026-05-03): partition on (source, external_id, title_clean,
# company_clean) — preserves rows where JSearch reused an external_id across
# *different* jobs (different title or different company). 862-group audit found
# ~10 such collisions (e.g. Epic/ICR, Eaton/Jacobs, BD/Parsons) and ~24
# casing/aggregator variants. Aligns with the post-hash-fix steady state where
# the new ``[source, external_id, title, company]`` hash also admits these rows.
#
# The company-string normalization here is deliberately minimal (lower + trim
# trailing punctuation). Tighter canonicalization (state-suffix stripping,
# corporate-form collapse, typo handling) is deferred to JIE#366. Within the
# JIE#209 cleanup that means ~24 bucket-C rows (casing variants like
# 'CITY OF EL PASO, TX' vs 'City of El Paso') are conservatively kept in the
# audit table; downstream ``job_postings`` already collapses them via the
# promotion-side fuzzy company match, so analytics are not skewed.

# Excess raw_ingested_jobs: per (source, external_id, title_clean, company_clean),
# all rows except the earliest by ingestion_timestamp (tie-break: lowest id).
_EXCESS_RAW_SQL = text(
    """
    WITH ranked AS (
      SELECT id, source, external_id, ingestion_timestamp,
             ROW_NUMBER() OVER (
               PARTITION BY
                 source,
                 external_id,
                 LOWER(BTRIM(coalesce(title, ''))),
                 LOWER(BTRIM(REGEXP_REPLACE(coalesce(company, ''), '[[:punct:]]+$', '')))
               ORDER BY ingestion_timestamp ASC, id ASC
             ) AS rn
      FROM dbo.raw_ingested_jobs
    )
    SELECT id FROM ranked WHERE rn > 1
    """
)

# Excess normalized_jobs: same partition shape on the normalized rows.
_EXCESS_NORM_SQL = text(
    """
    WITH ranked AS (
      SELECT id, source, external_id, created_at,
             ROW_NUMBER() OVER (
               PARTITION BY
                 source,
                 external_id,
                 LOWER(BTRIM(coalesce(title, ''))),
                 LOWER(BTRIM(REGEXP_REPLACE(coalesce(company, ''), '[[:punct:]]+$', '')))
               ORDER BY created_at ASC, id ASC
             ) AS rn
      FROM dbo.normalized_jobs
    )
    SELECT id FROM ranked WHERE rn > 1
    """
)


def fetch_excess_ids() -> dict[str, list[int]]:
    """Return the IDs that would be deleted, keyed by table.

    Read-only; safe to call repeatedly.
    """
    engine = get_engine()
    with engine.connect() as conn:
        excess_raw = [int(r[0]) for r in conn.execute(_EXCESS_RAW_SQL).fetchall()]
        excess_norm = [int(r[0]) for r in conn.execute(_EXCESS_NORM_SQL).fetchall()]

        if excess_norm:
            excess_extr = [
                int(r[0])
                for r in conn.execute(
                    text(
                        "SELECT id FROM dbo.extracted_intelligence "
                        "WHERE normalized_job_id = ANY(:ids)"
                    ),
                    {"ids": excess_norm},
                ).fetchall()
            ]
        else:
            excess_extr = []

    return {
        "raw_ingested_jobs": excess_raw,
        "normalized_jobs": excess_norm,
        "extracted_intelligence": excess_extr,
    }


def cleanup(*, apply: bool = False) -> dict[str, Any]:
    """Identify and (optionally) delete duplicate-driven rows.

    When ``apply=False`` (default), only counts and reports.
    """
    excess = fetch_excess_ids()
    raw_ids = excess["raw_ingested_jobs"]
    norm_ids = excess["normalized_jobs"]
    extr_ids = excess["extracted_intelligence"]

    summary = {
        "raw_ingested_jobs_excess": len(raw_ids),
        "normalized_jobs_excess": len(norm_ids),
        "extracted_intelligence_excess": len(extr_ids),
        "applied": apply,
        "deleted_raw": 0,
        "deleted_norm": 0,
        "deleted_extr": 0,
    }

    log.info("cleanup_scope", **{k: v for k, v in summary.items() if k.endswith("_excess")})

    if not raw_ids and not norm_ids and not extr_ids:
        log.info("cleanup_no_candidates")
        return summary

    if not apply:
        # Preview a few sample IDs
        for tbl, ids in (
            ("raw_ingested_jobs", raw_ids),
            ("normalized_jobs", norm_ids),
            ("extracted_intelligence", extr_ids),
        ):
            preview = ids[:10]
            print(f"  [dry-run] {tbl}: {len(ids)} rows would be deleted "
                  f"(sample ids: {preview}{'...' if len(ids) > 10 else ''})")
        return summary

    # FK order: extracted_intelligence (children) → normalized_jobs → raw_ingested_jobs.
    # Single transaction; full rollback on any error.
    with session_scope() as session:
        if extr_ids:
            res = session.execute(
                text("DELETE FROM dbo.extracted_intelligence WHERE id = ANY(:ids)"),
                {"ids": extr_ids},
            )
            summary["deleted_extr"] = res.rowcount or 0
            log.info(
                "cleanup_deleted_extracted_intelligence",
                rowcount=summary["deleted_extr"],
                expected=len(extr_ids),
                reason="jie209_dedup_cleanup",
            )

        if norm_ids:
            res = session.execute(
                text("DELETE FROM dbo.normalized_jobs WHERE id = ANY(:ids)"),
                {"ids": norm_ids},
            )
            summary["deleted_norm"] = res.rowcount or 0
            log.info(
                "cleanup_deleted_normalized_jobs",
                rowcount=summary["deleted_norm"],
                expected=len(norm_ids),
                reason="jie209_dedup_cleanup",
            )

        if raw_ids:
            res = session.execute(
                text("DELETE FROM dbo.raw_ingested_jobs WHERE id = ANY(:ids)"),
                {"ids": raw_ids},
            )
            summary["deleted_raw"] = res.rowcount or 0
            log.info(
                "cleanup_deleted_raw_ingested_jobs",
                rowcount=summary["deleted_raw"],
                expected=len(raw_ids),
                reason="jie209_dedup_cleanup",
            )

    log.info("cleanup_complete", **summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-shot cleanup of bug-introduced duplicate rows in raw_ingested_jobs, "
            "normalized_jobs, and extracted_intelligence (JIE #209). "
            "Default is dry-run; pass --apply to perform deletions. "
            "Preserves llm_audit_log and job_postings."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform deletes (without this flag, runs in dry-run mode).",
    )
    args = parser.parse_args()

    load_repo_root_dotenv()
    summary = cleanup(apply=args.apply)

    print()
    print("=" * 64)
    print(f"JIE #209 cleanup ({'APPLY' if args.apply else 'DRY-RUN'}):")
    print(f"  raw_ingested_jobs    excess: {summary['raw_ingested_jobs_excess']:>6}")
    print(f"  normalized_jobs      excess: {summary['normalized_jobs_excess']:>6}")
    print(f"  extracted_intelligence excess: {summary['extracted_intelligence_excess']:>6}")
    if args.apply:
        print(f"  raw_ingested_jobs    deleted: {summary['deleted_raw']:>6}")
        print(f"  normalized_jobs      deleted: {summary['deleted_norm']:>6}")
        print(f"  extracted_intelligence deleted: {summary['deleted_extr']:>6}")
    print(f"  llm_audit_log        : preserved (not touched)")
    print(f"  job_postings         : preserved (not touched)")
    print("=" * 64)

    if args.apply:
        # Sanity: deletion counts should match identification counts
        if (
            summary["deleted_raw"] != summary["raw_ingested_jobs_excess"]
            or summary["deleted_norm"] != summary["normalized_jobs_excess"]
            or summary["deleted_extr"] != summary["extracted_intelligence_excess"]
        ):
            print("WARNING: deleted rowcount differs from identified rowcount")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
