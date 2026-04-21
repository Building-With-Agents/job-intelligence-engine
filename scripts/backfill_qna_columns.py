"""Deterministic backfill for Week 8 Q&A-ready columns on dbo.job_postings.

Closes the data half of issues #172, #173, #174 (PR #195 added the schema/promotion
half — this script populates existing rows without re-running enrichment).

What this script does
---------------------
For every row in ``dbo.job_postings`` where the target column is NULL, it fills
the value from a deterministic source — no LLM calls, no enrichment re-run.

| Column                | Source                                                      |
|-----------------------|-------------------------------------------------------------|
| date_posted           | normalized_jobs.date_posted (typed timestamptz)             |
| is_remote             | normalized_jobs.is_remote                                   |
| salary_min            | normalized_jobs.salary_min                                  |
| salary_max            | normalized_jobs.salary_max                                  |
| salary_currency       | normalized_jobs.salary_currency                             |
| salary_period         | normalized_jobs.salary_period                               |
| seniority_level       | (a) normalized_jobs.experience_level mapped to closed set;  |
|                       | (b) classify_seniority() regex fallback over title +        |
|                       |     description + extracted_intelligence tasks/responsibil. |
| role_classification   | classify_role() over title + corpus, against DB-seeded      |
|                       | technology_areas + industry_sectors                         |

All operations are idempotent: only rows where the target column IS NULL are touched.

Safety
------
- Read-only against ``raw_ingested_jobs``, ``normalized_jobs``,
  ``extracted_intelligence``, ``technology_areas``, ``industry_sectors``.
- Writes only to ``dbo.job_postings`` and only via UPDATE (never DELETE / TRUNCATE).
- Per-pair audit tables (raw_ingested_jobs, llm_audit_log, etc.) are never modified.

Usage
-----
::

    # Preview what would change (no writes)
    python scripts/backfill_qna_columns.py --dry-run

    # Backfill all 8 columns
    python scripts/backfill_qna_columns.py

    # Backfill only specific columns (comma-separated)
    python scripts/backfill_qna_columns.py --columns date_posted,is_remote

    # Limit batch size for the per-row passes (default 500)
    python scripts/backfill_qna_columns.py --batch-size 200

Repairing misclassified ``role_classification`` (e.g. after taxonomy/classifier fixes)
---------------------------------------------------------------------------------------
This script only **fills NULLs**; it does not overwrite existing non-NULL values.
To re-apply classification for rows already labeled (for example
``N/A Not an IT role`` from an older ``classify_role`` sector fallback):

1. Optionally narrow the set with a stricter ``WHERE`` clause.
2. Reset labels to NULL, e.g.
   ``UPDATE dbo.job_postings SET role_classification = NULL WHERE role_classification = 'N/A Not an IT role';``
3. Preview: ``python scripts/backfill_qna_columns.py --dry-run --columns role_classification``
4. Apply: ``python scripts/backfill_qna_columns.py --columns role_classification``

Re-running the backfill after a NULL reset is **idempotent** for those rows (same NULL-only
rules). You do **not** need ``docker compose down -v`` or any volume wipe for this repair.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from common.data_store.database import get_engine
from enrichment.classification import (
    build_job_corpus,
    classify_role,
    classify_seniority,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("backfill_qna_columns")

# All eight target columns, grouped by backfill strategy.
BULK_COLUMNS_FROM_NORMALIZED = (
    "date_posted",
    "is_remote",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
)
PER_ROW_COLUMNS = ("seniority_level", "role_classification")
ALL_COLUMNS = BULK_COLUMNS_FROM_NORMALIZED + PER_ROW_COLUMNS

# Mapping from JSearch-style experience_level strings to the closed seniority set
# the regex classifier produces. Anything not mapped falls through to the regex pass.
EXPERIENCE_LEVEL_TO_SENIORITY = {
    "INTERNSHIP": "intern",
    "INTERN": "intern",
    "ENTRY_LEVEL": "junior",
    "ENTRY": "junior",
    "JUNIOR": "junior",
    "ASSOCIATE": "junior",
    "MID_LEVEL": "mid",
    "MID": "mid",
    "MID_SENIOR_LEVEL": "mid",
    "SENIOR_LEVEL": "senior",
    "SENIOR": "senior",
    "LEAD": "lead",
    "PRINCIPAL": "lead",
    "STAFF": "lead",
    "EXECUTIVE": "executive",
    "DIRECTOR": "executive",
    "VP": "executive",
    "C_SUITE": "executive",
    "CEO": "executive",
    "CTO": "executive",
}


# ---------------------------------------------------------------------------
# Bulk pass: date_posted, is_remote, salary_min, salary_max, salary_currency, salary_period
# ---------------------------------------------------------------------------

# A single multi-column UPDATE FROM join. Each column is wrapped in COALESCE so we
# never overwrite a non-NULL value already on the row (idempotent).
# NOTE on duplicates: dbo.normalized_jobs has ~100 duplicate (source, external_id) pairs
# (re-ingested rows with different id). All three backfill queries pick the latest (MAX(id))
# normalized_jobs row per (source, external_id) deterministically via LATERAL ... LIMIT 1.

_BULK_UPDATE_SQL = text(
    """
    UPDATE dbo.job_postings jp SET
        date_posted     = COALESCE(jp.date_posted,     nj.date_posted),
        is_remote       = COALESCE(jp.is_remote,       nj.is_remote),
        salary_min      = COALESCE(jp.salary_min,      nj.salary_min),
        salary_max      = COALESCE(jp.salary_max,      nj.salary_max),
        salary_currency = COALESCE(jp.salary_currency, nj.salary_currency),
        salary_period   = COALESCE(jp.salary_period,   nj.salary_period)
    FROM (
        SELECT DISTINCT ON (source, external_id)
            source, external_id, date_posted, is_remote,
            salary_min, salary_max, salary_currency, salary_period
        FROM dbo.normalized_jobs
        ORDER BY source, external_id, id DESC
    ) nj
    WHERE jp.source = nj.source
      AND jp.external_id = nj.external_id
      AND (
          jp.date_posted     IS NULL
       OR jp.is_remote       IS NULL
       OR jp.salary_min      IS NULL
       OR jp.salary_max      IS NULL
       OR jp.salary_currency IS NULL
       OR jp.salary_period   IS NULL
      )
    """
)

_BULK_DRYRUN_SQL = text(
    """
    SELECT
        SUM(CASE WHEN jp.date_posted     IS NULL AND nj.date_posted     IS NOT NULL THEN 1 ELSE 0 END) AS date_posted,
        SUM(CASE WHEN jp.is_remote       IS NULL AND nj.is_remote       IS NOT NULL THEN 1 ELSE 0 END) AS is_remote,
        SUM(CASE WHEN jp.salary_min      IS NULL AND nj.salary_min      IS NOT NULL THEN 1 ELSE 0 END) AS salary_min,
        SUM(CASE WHEN jp.salary_max      IS NULL AND nj.salary_max      IS NOT NULL THEN 1 ELSE 0 END) AS salary_max,
        SUM(CASE WHEN jp.salary_currency IS NULL AND nj.salary_currency IS NOT NULL THEN 1 ELSE 0 END) AS salary_currency,
        SUM(CASE WHEN jp.salary_period   IS NULL AND nj.salary_period   IS NOT NULL THEN 1 ELSE 0 END) AS salary_period
    FROM dbo.job_postings jp
    JOIN (
        SELECT DISTINCT ON (source, external_id)
            source, external_id, date_posted, is_remote,
            salary_min, salary_max, salary_currency, salary_period
        FROM dbo.normalized_jobs
        ORDER BY source, external_id, id DESC
    ) nj
      ON jp.source = nj.source
     AND jp.external_id = nj.external_id
    """
)


def backfill_bulk_columns(session: Session, *, dry_run: bool) -> dict[str, int]:
    """Bulk-fill the 6 normalized-source columns. Returns per-column would-fill counts.

    The per-column counts are taken from a snapshot BEFORE the UPDATE runs (dry-run SQL).
    The actual UPDATE may also include rows where some columns were already non-NULL
    (COALESCE preserves them). The main() before/after snapshot is the source of truth
    for the live-mode delta report.
    """
    pre = session.execute(_BULK_DRYRUN_SQL).mappings().first()
    counts = {col: int(pre[col] or 0) for col in BULK_COLUMNS_FROM_NORMALIZED}
    if dry_run:
        return counts
    result = session.execute(_BULK_UPDATE_SQL)
    session.commit()
    log.info("bulk_pass_complete rows_touched=%d would_fill=%s", result.rowcount or 0, counts)
    return counts


# ---------------------------------------------------------------------------
# Per-row pass: seniority_level (hybrid: experience_level mapping → regex classifier)
# ---------------------------------------------------------------------------

_SENIORITY_FROM_EXPERIENCE_LEVEL_SQL = text(
    """
    UPDATE dbo.job_postings jp
    SET seniority_level = sub.mapped
    FROM (
        SELECT
            jp2.job_posting_id,
            CASE UPPER(TRIM(nj.experience_level))
                WHEN 'INTERNSHIP'        THEN 'intern'
                WHEN 'INTERN'            THEN 'intern'
                WHEN 'ENTRY_LEVEL'       THEN 'junior'
                WHEN 'ENTRY'             THEN 'junior'
                WHEN 'JUNIOR'            THEN 'junior'
                WHEN 'ASSOCIATE'         THEN 'junior'
                WHEN 'MID_LEVEL'         THEN 'mid'
                WHEN 'MID'               THEN 'mid'
                WHEN 'MID_SENIOR_LEVEL'  THEN 'mid'
                WHEN 'SENIOR_LEVEL'      THEN 'senior'
                WHEN 'SENIOR'            THEN 'senior'
                WHEN 'LEAD'              THEN 'lead'
                WHEN 'PRINCIPAL'         THEN 'lead'
                WHEN 'STAFF'             THEN 'lead'
                WHEN 'EXECUTIVE'         THEN 'executive'
                WHEN 'DIRECTOR'          THEN 'executive'
                WHEN 'VP'                THEN 'executive'
                WHEN 'C_SUITE'           THEN 'executive'
                WHEN 'CEO'               THEN 'executive'
                WHEN 'CTO'               THEN 'executive'
                ELSE NULL
            END AS mapped
        FROM dbo.job_postings jp2
        JOIN LATERAL (
            SELECT experience_level FROM dbo.normalized_jobs
            WHERE source = jp2.source AND external_id = jp2.external_id
            ORDER BY id DESC LIMIT 1
        ) nj ON TRUE
        WHERE jp2.seniority_level IS NULL
          AND nj.experience_level IS NOT NULL
    ) sub
    WHERE jp.job_posting_id = sub.job_posting_id
      AND sub.mapped IS NOT NULL
    """
)

_FETCH_SENIORITY_CANDIDATES_SQL = text(
    """
    SELECT
        jp.job_posting_id::text  AS job_posting_id,
        jp.job_title             AS job_title,
        jp.job_description       AS job_description,
        ei.skills                AS skills,
        ei.tools                 AS tools,
        ei.tasks                 AS tasks,
        ei.responsibilities      AS responsibilities,
        ei.context               AS context
    FROM dbo.job_postings jp
    LEFT JOIN LATERAL (
        SELECT id FROM dbo.normalized_jobs
        WHERE source = jp.source AND external_id = jp.external_id
        ORDER BY id DESC LIMIT 1
    ) nj ON TRUE
    LEFT JOIN LATERAL (
        SELECT skills, tools, tasks, responsibilities, context
        FROM dbo.extracted_intelligence
        WHERE normalized_job_id = nj.id
        ORDER BY id DESC
        LIMIT 1
    ) ei ON TRUE
    WHERE jp.seniority_level IS NULL
    ORDER BY jp.job_posting_id
    """
)

_UPDATE_SENIORITY_SQL = text(
    "UPDATE dbo.job_postings SET seniority_level = :value "
    "WHERE job_posting_id::text = :job_posting_id AND seniority_level IS NULL"
)


def backfill_seniority(
    session: Session,
    *,
    dry_run: bool,
    batch_size: int,
) -> dict[str, int]:
    """Hybrid seniority backfill: experience_level mapping → regex classifier fallback."""
    counts: dict[str, int] = {"experience_level_mapped": 0, "regex_classified": 0, "still_null": 0}

    # Pass A — bulk SQL UPDATE from experience_level mapping.
    if dry_run:
        preview = session.execute(
            text(
                """
                SELECT COUNT(*) AS n
                FROM dbo.job_postings jp
                JOIN LATERAL (
                    SELECT experience_level FROM dbo.normalized_jobs
                    WHERE source = jp.source AND external_id = jp.external_id
                    ORDER BY id DESC LIMIT 1
                ) nj ON TRUE
                WHERE jp.seniority_level IS NULL
                  AND nj.experience_level IS NOT NULL
                  AND UPPER(TRIM(nj.experience_level)) IN (
                    'INTERNSHIP','INTERN','ENTRY_LEVEL','ENTRY','JUNIOR','ASSOCIATE',
                    'MID_LEVEL','MID','MID_SENIOR_LEVEL','SENIOR_LEVEL','SENIOR',
                    'LEAD','PRINCIPAL','STAFF','EXECUTIVE','DIRECTOR','VP','C_SUITE','CEO','CTO'
                  )
                """
            )
        ).mappings().first()
        counts["experience_level_mapped"] = int(preview["n"] or 0)
    else:
        result = session.execute(_SENIORITY_FROM_EXPERIENCE_LEVEL_SQL)
        session.commit()
        counts["experience_level_mapped"] = result.rowcount or 0
        log.info("seniority pass A (experience_level mapping): %d filled", counts["experience_level_mapped"])

    # Pass B — Python regex classifier on remaining NULLs. Load all candidates upfront
    # (single snapshot — no pagination drift from concurrent updates).
    candidates = session.execute(_FETCH_SENIORITY_CANDIDATES_SQL).mappings().all()
    log.info("seniority pass B candidates after pass A: %d", len(candidates))

    pending_updates = 0
    for row in candidates:
        extraction: dict[str, Any] = {
            "skills": row.get("skills"),
            "tools": row.get("tools"),
            "tasks": row.get("tasks"),
            "responsibilities": row.get("responsibilities"),
            "context": row.get("context"),
        }
        value = classify_seniority(
            row.get("job_title") or "",
            row.get("job_description"),
            extraction,
        )
        if value == "unknown":
            # Don't write 'unknown' — leave NULL so a future enrichment can fill it.
            continue
        if not dry_run:
            session.execute(
                _UPDATE_SENIORITY_SQL,
                {"value": value, "job_posting_id": row["job_posting_id"]},
            )
        counts["regex_classified"] += 1
        pending_updates += 1
        if pending_updates >= batch_size and not dry_run:
            session.commit()
            log.info("seniority pass B committed batch (%d updates so far)", counts["regex_classified"])
            pending_updates = 0

    if pending_updates and not dry_run:
        session.commit()

    # Final remaining count
    remaining = session.execute(
        text("SELECT COUNT(*) AS n FROM dbo.job_postings WHERE seniority_level IS NULL")
    ).mappings().first()
    counts["still_null"] = int(remaining["n"] or 0)
    return counts


# ---------------------------------------------------------------------------
# Per-row pass: role_classification (regex classifier over title + corpus)
# ---------------------------------------------------------------------------

_FETCH_ROLE_CANDIDATES_SQL = text(
    """
    SELECT
        jp.job_posting_id::text  AS job_posting_id,
        jp.job_title             AS job_title,
        jp.job_description       AS job_description,
        ei.skills                AS skills,
        ei.tools                 AS tools,
        ei.tasks                 AS tasks,
        ei.responsibilities      AS responsibilities,
        ei.context               AS context
    FROM dbo.job_postings jp
    LEFT JOIN LATERAL (
        SELECT id FROM dbo.normalized_jobs
        WHERE source = jp.source AND external_id = jp.external_id
        ORDER BY id DESC LIMIT 1
    ) nj ON TRUE
    LEFT JOIN LATERAL (
        SELECT skills, tools, tasks, responsibilities, context
        FROM dbo.extracted_intelligence
        WHERE normalized_job_id = nj.id
        ORDER BY id DESC
        LIMIT 1
    ) ei ON TRUE
    WHERE jp.role_classification IS NULL
    ORDER BY jp.job_posting_id
    """
)

_UPDATE_ROLE_SQL = text(
    "UPDATE dbo.job_postings SET role_classification = :value "
    "WHERE job_posting_id::text = :job_posting_id AND role_classification IS NULL"
)


def backfill_role_classification(
    session: Session,
    *,
    dry_run: bool,
    batch_size: int,
) -> dict[str, int]:
    """Backfill role_classification using classify_role() over the corpus."""
    # Load reference taxonomies once.
    technology_areas = [
        (str(r["id"]), r["title"])
        for r in session.execute(
            text("SELECT id::text AS id, title FROM dbo.technology_areas WHERE title IS NOT NULL")
        ).mappings().all()
    ]
    industry_sectors = [
        (str(r["id"]), r["title"])
        for r in session.execute(
            text(
                "SELECT industry_sector_id::text AS id, sector_title AS title "
                "FROM dbo.industry_sectors WHERE sector_title IS NOT NULL"
            )
        ).mappings().all()
    ]
    log.info(
        "role_classification taxonomies loaded: %d technology_areas, %d industry_sectors",
        len(technology_areas), len(industry_sectors),
    )

    counts: dict[str, int] = {"classified": 0, "unclassified_written": 0, "still_null": 0}

    # Load all candidates upfront (single snapshot).
    candidates = session.execute(_FETCH_ROLE_CANDIDATES_SQL).mappings().all()
    log.info("role pass candidates: %d", len(candidates))

    pending_updates = 0
    for row in candidates:
        extraction: dict[str, Any] = {
            "skills": row.get("skills"),
            "tools": row.get("tools"),
            "tasks": row.get("tasks"),
            "responsibilities": row.get("responsibilities"),
            "context": row.get("context"),
        }
        corpus = build_job_corpus(
            row.get("job_title") or "",
            row.get("job_description"),
            extraction,
        )
        value = classify_role(
            row.get("job_title") or "",
            corpus,
            technology_areas,
            industry_sectors,
        )
        # classify_role returns "unclassified" for low-confidence — we still write it
        # so the row is no longer NULL (matches enrichment semantics).
        if value == "unclassified":
            counts["unclassified_written"] += 1
        else:
            counts["classified"] += 1
        if not dry_run:
            session.execute(
                _UPDATE_ROLE_SQL,
                {"value": value, "job_posting_id": row["job_posting_id"]},
            )
        pending_updates += 1
        if pending_updates >= batch_size and not dry_run:
            session.commit()
            log.info(
                "role pass committed batch (%d classified, %d unclassified so far)",
                counts["classified"], counts["unclassified_written"],
            )
            pending_updates = 0

    if pending_updates and not dry_run:
        session.commit()

    remaining = session.execute(
        text("SELECT COUNT(*) AS n FROM dbo.job_postings WHERE role_classification IS NULL")
    ).mappings().first()
    counts["still_null"] = int(remaining["n"] or 0)
    return counts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_columns(arg: str | None) -> tuple[str, ...]:
    if not arg:
        return ALL_COLUMNS
    requested = tuple(c.strip() for c in arg.split(",") if c.strip())
    unknown = [c for c in requested if c not in ALL_COLUMNS]
    if unknown:
        raise SystemExit(f"Unknown columns: {unknown}. Valid: {ALL_COLUMNS}")
    return requested


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows that would change without writing. Safe to run anytime.",
    )
    parser.add_argument(
        "--columns",
        type=str,
        default=None,
        help=f"Comma-separated subset to backfill. Default: all 8. Valid: {','.join(ALL_COLUMNS)}",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Per-row pass batch size (seniority_level + role_classification).",
    )
    args = parser.parse_args()

    columns = _parse_columns(args.columns)
    log.info("Starting backfill: columns=%s dry_run=%s batch_size=%d", columns, args.dry_run, args.batch_size)
    started = time.monotonic()

    engine = get_engine()
    with Session(engine) as session:
        # Snapshot NULL counts BEFORE any writes so the report shows real deltas.
        before = {}
        for col in ALL_COLUMNS:
            row = session.execute(
                text(f"SELECT COUNT(*) AS n FROM dbo.job_postings WHERE {col} IS NULL")
            ).mappings().first()
            before[col] = int(row["n"] or 0)
        log.info("NULL counts BEFORE: %s", before)

        # Bulk pass for normalized-sourced columns.
        bulk_targets = [c for c in BULK_COLUMNS_FROM_NORMALIZED if c in columns]
        if bulk_targets:
            log.info("Bulk pass for: %s", bulk_targets)
            bulk_counts = backfill_bulk_columns(session, dry_run=args.dry_run)
            if args.dry_run:
                log.info("bulk projection (would-fill per column from normalized_jobs): %s", bulk_counts)

        # seniority_level
        if "seniority_level" in columns:
            log.info("Seniority pass (hybrid)...")
            sen_counts = backfill_seniority(session, dry_run=args.dry_run, batch_size=args.batch_size)
            log.info("seniority counts: %s", sen_counts)

        # role_classification
        if "role_classification" in columns:
            log.info("Role classification pass...")
            role_counts = backfill_role_classification(session, dry_run=args.dry_run, batch_size=args.batch_size)
            log.info("role counts: %s", role_counts)

        # Snapshot NULL counts AFTER.
        after = {}
        for col in ALL_COLUMNS:
            row = session.execute(
                text(f"SELECT COUNT(*) AS n FROM dbo.job_postings WHERE {col} IS NULL")
            ).mappings().first()
            after[col] = int(row["n"] or 0)
        log.info("NULL counts AFTER: %s", after)

        delta = {col: before[col] - after[col] for col in ALL_COLUMNS}
        log.info("Filled per column: %s", delta)

    elapsed = time.monotonic() - started
    log.info("Backfill complete in %.1fs (dry_run=%s)", elapsed, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
