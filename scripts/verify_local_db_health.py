#!/usr/bin/env python3
"""Local PostgreSQL health checks for dev and pre-demo runs (schema drift, Q&A coverage, referential integrity).

Run from repo root (venv on)::

    python scripts/verify_local_db_health.py

Reads ``PYTHON_DATABASE_URL`` from repo-root ``.env`` via :func:`common.env.load_repo_root_dotenv`.

Exit code: **0** if there are no ERROR-level findings; **1** if any ERROR. WARN lines do not fail the process.

Tuning: adjust named constants below (no CLI flags). Sections 3b–3d run queries that may **sequentially scan**
large tables (orphans, duplicates, role buckets); acceptable for local verification.

**ERROR vs WARN**

- **ERROR:** Missing required Q&A columns; unexpected SQL failures for required checks; referential orphans
  (``extracted_intelligence.normalized_job_id`` not in ``normalized_jobs``); dangling ``employer_profile_id``;
  duplicate ``(source, external_id)`` on ``job_postings`` when both non-null; duplicate ``raw_payload_hash``;
  ``salary_min > salary_max`` when both non-null.
- **WARN:** Soft row-count floors; Q&A non-null floors; schema drift hints; bad ``role_classification``
  distribution (#197); ``ingestion_run_id`` not in ``job_ingestion_runs``; count gaps vs fixtures; Q&A quality
  heuristics; missing extensions; index/type spot-checks; empty analytics tables; large table sizes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from common.data_store.database import get_engine
from common.env import load_repo_root_dotenv

load_repo_root_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[1]
METADATA_JSON = REPO_ROOT / "scripts" / "pg-seed-data" / "fixtures" / "metadata.json"

# --- Tunable thresholds (no CLI) -------------------------------------------------

# Soft floors for Q&A columns on dbo.job_postings (below → WARN).
QNA_COLUMN_FLOORS: dict[str, int] = {
    "date_posted": 1_500,
    "seniority_level": 2_500,
    "is_remote": 1_500,
    "role_classification": 2_800,
    "salary_min": 400,
    "salary_max": 400,
    "salary_currency": 400,
    "salary_period": 400,
}

# Minimum row counts for core tables (below → WARN).
TABLE_COUNT_FLOORS: dict[str, int] = {
    "job_postings": 1_000,
    "normalized_jobs": 500,
    "extracted_intelligence": 500,
    "raw_ingested_jobs": 500,
    "employer_profiles": 500,
    "companies": 500,
}

# role_classification distribution (#197): "N/A Not an IT role" must not dominate.
NA_IT_ROLE_LABEL = "N/A Not an IT role"
UNCLASSIFIED_LABEL = "unclassified"

# WARN if this label's share of non-null role_classification rows exceeds the fraction.
NA_IT_ROLE_MAX_FRACTION = 0.12
# WARN if unclassified share of non-null role_classification exceeds this (optional guard).
UNCLASSIFIED_MAX_FRACTION = 0.35

# normalized_jobs vs extracted_intelligence (expect 1:1); WARN if gap exceeds either bound.
NJ_EI_WARN_ABS = 50
NJ_EI_WARN_REL = 0.02

# raw_ingested_jobs vs normalized_jobs: pipeline may stage more than normalizes; WARN on large skew.
RAW_NJ_WARN_ABS = 400
RAW_NJ_WARN_REL = 0.15

# Fixture comparison: WARN if job_postings count is below metadata count * this factor (seed shrink).
JOB_POSTINGS_METADATA_MIN_FRACTION = 0.85

# Seniority: WARN if the top single value holds more than this share of non-null rows.
SENIORITY_TOP_BUCKET_MAX_FRACTION = 0.45

# date_posted: WARN if NULL share exceeds this (of total postings).
DATE_POSTED_NULL_MAX_FRACTION = 0.25
# WARN if more than this fraction of non-null date_posted are strictly in the future.
DATE_POSTED_FUTURE_MAX_FRACTION = 0.02
# WARN if the most common calendar day holds more than this fraction of non-null date_posted.
DATE_POSTED_SINGLE_DAY_CONCENTRATION_MAX = 0.35

# is_remote: WARN if non-null share is below this fraction of total postings.
IS_REMOTE_MIN_COVERAGE_FRACTION = 0.35

# Pipeline tables: WARN total relation size above this (bytes). ~512 MiB local default.
TABLE_SIZE_WARN_BYTES = 512 * 1024 * 1024

# Expected PostgreSQL extensions (WARN if missing).
EXPECTED_EXTENSIONS = ("vector", "uuid-ossp")

# Q&A-related column names — indexes whose pg_indexes.indexdef mentions any of these pass the smoke check.
QNA_INDEX_COLUMN_MARKERS: tuple[str, ...] = (
    "date_posted",
    "seniority_level",
    "is_remote",
    "role_classification",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
)

# --- Pure helpers (unit-tested) ---------------------------------------------------


def fraction(numer: int, denom: int) -> float:
    """Safe ratio in [0, 1]."""
    if denom <= 0:
        return 0.0
    return float(numer) / float(denom)


def gap_triggers_warn(abs_delta: int, larger_base: int, *, max_abs: int, max_rel: float) -> bool:
    """True if absolute delta or relative gap vs ``larger_base`` exceeds thresholds."""
    if abs_delta > max_abs:
        return True
    if larger_base <= 0:
        return False
    return (abs_delta / float(larger_base)) > max_rel


def _emit(level: str, msg: str, lines: list[tuple[str, str]]) -> None:
    lines.append((level, msg))
    sym = {"OK": "[OK]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, level)
    print(f"  {sym} {msg}")


def _load_metadata_counts() -> dict[str, int] | None:
    if not METADATA_JSON.is_file():
        return None
    try:
        data = json.loads(METADATA_JSON.read_text(encoding="utf-8"))
        raw = data.get("counts")
        if not isinstance(raw, dict):
            return None
        return {str(k): int(v) for k, v in raw.items()}
    except (OSError, ValueError, TypeError):
        return None


def main() -> int:
    print("=== verify_local_db_health ===\n")

    lines: list[tuple[str, str]] = []
    engine = get_engine()
    meta_counts = _load_metadata_counts()

    with engine.connect() as conn:
        # ----- 3a Baseline -----
        print("3a) Baseline: core counts, Q&A columns, drift, role_classification")
        for tbl, floor in TABLE_COUNT_FLOORS.items():
            try:
                n = conn.execute(text(f"SELECT COUNT(*) FROM dbo.{tbl}")).scalar() or 0
                if n < floor:
                    _emit("WARN", f"{tbl}: {n:,} rows (floor {floor:,})", lines)
                else:
                    _emit("OK", f"{tbl}: {n:,} rows", lines)
            except Exception as exc:
                _emit("ERROR", f"{tbl}: {exc}", lines)

        qna_cols = tuple(QNA_COLUMN_FLOORS.keys())
        try:
            in_list = ", ".join(f"'{c}'" for c in qna_cols)
            rows = conn.execute(
                text(
                    f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo' AND table_name = 'job_postings'
                      AND column_name IN ({in_list})
                    """
                )
            ).fetchall()
            found = {r[0] for r in rows}
            for c in qna_cols:
                if c not in found:
                    _emit("ERROR", f"missing column on job_postings: {c}", lines)
                else:
                    _emit("OK", f"column exists: {c}", lines)
        except Exception as exc:
            _emit("ERROR", f"could not inspect job_postings columns: {exc}", lines)

        try:
            row = conn.execute(
                text(
                    """
                    SELECT
                      COUNT(*) AS total_postings,
                      COUNT(date_posted) AS date_posted,
                      COUNT(seniority_level) AS seniority_level,
                      COUNT(is_remote) AS is_remote,
                      COUNT(role_classification) AS role_classification,
                      COUNT(salary_min) AS salary_min,
                      COUNT(salary_max) AS salary_max,
                      COUNT(salary_currency) AS salary_currency,
                      COUNT(salary_period) AS salary_period
                    FROM dbo.job_postings
                    """
                )
            ).mappings().one()
            total = row["total_postings"]
            print(f"     total job_postings: {total:,}")
            for col in qna_cols:
                n = row[col]
                floor = QNA_COLUMN_FLOORS[col]
                if n < floor:
                    _emit(
                        "WARN",
                        f"{col}: {n:,} non-null (floor {floor:,}) — consider backfill",
                        lines,
                    )
                else:
                    _emit("OK", f"{col}: {n:,} non-null", lines)
        except Exception as exc:
            _emit("ERROR", f"Q&A non-null count query failed: {exc}", lines)

        # Schema drift: job_ingestion_runs.id
        try:
            r = conn.execute(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo' AND table_name = 'job_ingestion_runs'
                      AND column_name = 'id'
                    """
                )
            ).first()
            if r is None:
                _emit("WARN", "job_ingestion_runs.id: column not found", lines)
            elif str(r[0]).lower() in ("integer", "bigint", "smallint"):
                _emit(
                    "WARN",
                    "job_ingestion_runs.id is integer-like — fixtures expect UUID; re-seed/migrate",
                    lines,
                )
            elif str(r[0]).lower() == "uuid":
                _emit("OK", "job_ingestion_runs.id type is uuid", lines)
            else:
                _emit("WARN", f"job_ingestion_runs.id type is {r[0]!r}", lines)
        except Exception as exc:
            _emit("ERROR", f"job_ingestion_runs id check: {exc}", lines)

        try:
            r = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo' AND table_name = 'orchestration_audit_log'
                      AND column_name = 'event_type'
                    """
                )
            ).first()
            if r:
                _emit("OK", "orchestration_audit_log.event_type exists", lines)
            else:
                _emit(
                    "WARN",
                    "orchestration_audit_log.event_type missing — older table shape",
                    lines,
                )
        except Exception as exc:
            _emit("WARN", f"orchestration_audit_log.event_type check skipped ({exc})", lines)

        # role_classification distribution (seq scan on job_postings)
        try:
            dist = conn.execute(
                text(
                    """
                    SELECT role_classification AS label, COUNT(*) AS c
                    FROM dbo.job_postings
                    WHERE role_classification IS NOT NULL
                    GROUP BY role_classification
                    ORDER BY c DESC
                    """
                )
            ).fetchall()
            classified_total = sum(int(r[1]) for r in dist)
            if classified_total == 0:
                _emit("WARN", "role_classification: no non-null rows", lines)
            else:
                top_label, top_c = dist[0][0], int(dist[0][1])
                top_share = fraction(top_c, classified_total)
                na_count = next((int(r[1]) for r in dist if r[0] == NA_IT_ROLE_LABEL), 0)
                na_share = fraction(na_count, classified_total)
                un_count = next((int(r[1]) for r in dist if r[0] == UNCLASSIFIED_LABEL), 0)
                un_share = fraction(un_count, classified_total)
                _emit(
                    "OK",
                    f"role_classification: {classified_total:,} classified; top={top_label!r} ({top_share:.1%})",
                    lines,
                )
                if top_label == NA_IT_ROLE_LABEL or na_share > NA_IT_ROLE_MAX_FRACTION:
                    _emit(
                        "WARN",
                        f"role_classification: {NA_IT_ROLE_LABEL!r} share {na_share:.1%} "
                        f"(max {NA_IT_ROLE_MAX_FRACTION:.0%}) or top bucket — check sector fallback (#197)",
                        lines,
                    )
                if un_share > UNCLASSIFIED_MAX_FRACTION:
                    _emit(
                        "WARN",
                        f"role_classification: {UNCLASSIFIED_LABEL!r} share {un_share:.1%} "
                        f"(max {UNCLASSIFIED_MAX_FRACTION:.0%})",
                        lines,
                    )
        except Exception as exc:
            _emit("ERROR", f"role_classification distribution: {exc}", lines)
        print()

        # ----- 3b Referential -----
        print("3b) Referential integrity")
        try:
            orphan_n = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM dbo.extracted_intelligence ei
                    LEFT JOIN dbo.normalized_jobs nj ON ei.normalized_job_id = nj.id
                    WHERE nj.id IS NULL
                    """
                )
            ).scalar() or 0
            if orphan_n > 0:
                _emit(
                    "ERROR",
                    f"extracted_intelligence orphans (normalized_job_id not in normalized_jobs): {orphan_n:,}",
                    lines,
                )
            else:
                _emit("OK", "extracted_intelligence: no orphaned normalized_job_id", lines)
        except Exception as exc:
            _emit("ERROR", f"orphan check failed: {exc}", lines)

        for tbl in ("normalized_jobs", "raw_ingested_jobs"):
            try:
                orphan_ids = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(DISTINCT ingestion_run_id) FROM dbo.{tbl}
                        WHERE ingestion_run_id NOT IN (SELECT run_id FROM dbo.job_ingestion_runs)
                        """
                    )
                ).scalar() or 0
                bad_rows = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) FROM dbo.{tbl}
                        WHERE ingestion_run_id NOT IN (SELECT run_id FROM dbo.job_ingestion_runs)
                        """
                    )
                ).scalar() or 0
                if orphan_ids > 0:
                    _emit(
                        "WARN",
                        f"{tbl}: {orphan_ids:,} distinct ingestion_run_id value(s) not in job_ingestion_runs "
                        f"({bad_rows:,} rows) — legacy or stale runs possible",
                        lines,
                    )
                else:
                    _emit("OK", f"{tbl}: all ingestion_run_id values resolve to job_ingestion_runs", lines)
            except Exception as exc:
                _emit("ERROR", f"{tbl} run linkage: {exc}", lines)

        try:
            dangle = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM dbo.job_postings jp
                    LEFT JOIN dbo.employer_profiles ep ON jp.employer_profile_id = ep.id
                    WHERE jp.employer_profile_id IS NOT NULL AND ep.id IS NULL
                    """
                )
            ).scalar() or 0
            if dangle > 0:
                _emit(
                    "ERROR",
                    f"job_postings with employer_profile_id but no employer_profiles row: {dangle:,}",
                    lines,
                )
            else:
                _emit("OK", "employer_profile_id: no dangling references", lines)
        except Exception as exc:
            _emit("ERROR", f"employer_profile linkage: {exc}", lines)
        print()

        # ----- 3c Row count consistency -----
        print("3c) Row count consistency")
        try:
            nj = conn.execute(text("SELECT COUNT(*) FROM dbo.normalized_jobs")).scalar() or 0
            ei = conn.execute(text("SELECT COUNT(*) FROM dbo.extracted_intelligence")).scalar() or 0
            delta = abs(nj - ei)
            larger = max(nj, ei)
            if gap_triggers_warn(delta, larger, max_abs=NJ_EI_WARN_ABS, max_rel=NJ_EI_WARN_REL):
                _emit(
                    "WARN",
                    f"normalized_jobs ({nj:,}) vs extracted_intelligence ({ei:,}) delta {delta:,} "
                    f"(warn if abs>{NJ_EI_WARN_ABS} or rel>{NJ_EI_WARN_REL:.0%})",
                    lines,
                )
            else:
                _emit("OK", f"normalized_jobs vs extracted_intelligence: delta {delta:,}", lines)
        except Exception as exc:
            _emit("ERROR", f"nj/ei counts: {exc}", lines)

        if meta_counts is None:
            _emit(
                "WARN",
                f"metadata fixture missing or unreadable: {METADATA_JSON} — skip job_postings vs fixture",
                lines,
            )
        else:
            try:
                jp = conn.execute(text("SELECT COUNT(*) FROM dbo.job_postings")).scalar() or 0
                expected = meta_counts.get("job_postings")
                if expected is not None:
                    floor = int(expected * JOB_POSTINGS_METADATA_MIN_FRACTION)
                    if jp < floor:
                        _emit(
                            "WARN",
                            f"job_postings {jp:,} below fixture-guided floor {floor:,} "
                            f"({JOB_POSTINGS_METADATA_MIN_FRACTION:.0%} of metadata {expected:,})",
                            lines,
                        )
                    else:
                        _emit(
                            "OK",
                            f"job_postings {jp:,} vs metadata {expected:,} (min {floor:,})",
                            lines,
                        )
            except Exception as exc:
                _emit("ERROR", f"job_postings vs metadata: {exc}", lines)

        try:
            raw_n = conn.execute(text("SELECT COUNT(*) FROM dbo.raw_ingested_jobs")).scalar() or 0
            nj_n = conn.execute(text("SELECT COUNT(*) FROM dbo.normalized_jobs")).scalar() or 0
            delta = abs(raw_n - nj_n)
            larger = max(raw_n, nj_n)
            if gap_triggers_warn(delta, larger, max_abs=RAW_NJ_WARN_ABS, max_rel=RAW_NJ_WARN_REL):
                _emit(
                    "WARN",
                    f"raw_ingested_jobs ({raw_n:,}) vs normalized_jobs ({nj_n:,}) delta {delta:,} "
                    f"(warn if abs>{RAW_NJ_WARN_ABS} or rel>{RAW_NJ_WARN_REL:.0%})",
                    lines,
                )
            else:
                _emit("OK", f"raw_ingested_jobs vs normalized_jobs: delta {delta:,}", lines)
        except Exception as exc:
            _emit("ERROR", f"raw vs normalized counts: {exc}", lines)
        print()

        # ----- 3d Duplicates -----
        print("3d) Duplicates")
        try:
            dup_groups = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT source, external_id
                      FROM dbo.job_postings
                      WHERE source IS NOT NULL AND external_id IS NOT NULL
                      GROUP BY source, external_id
                      HAVING COUNT(*) > 1
                    ) s
                    """
                )
            ).scalar() or 0
            dup_rows = conn.execute(
                text(
                    """
                    SELECT COALESCE(SUM(c - 1), 0) FROM (
                      SELECT COUNT(*) AS c
                      FROM dbo.job_postings
                      WHERE source IS NOT NULL AND external_id IS NOT NULL
                      GROUP BY source, external_id
                      HAVING COUNT(*) > 1
                    ) x
                    """
                )
            ).scalar() or 0
            if dup_groups > 0:
                _emit(
                    "ERROR",
                    f"job_postings duplicate (source, external_id): {dup_groups:,} key(s), ~{dup_rows:,} extra row(s)",
                    lines,
                )
            else:
                _emit("OK", "job_postings: no duplicate (source, external_id) keys", lines)
        except Exception as exc:
            _emit("ERROR", f"job_postings duplicate check: {exc}", lines)

        try:
            hash_dupes = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT raw_payload_hash FROM dbo.raw_ingested_jobs
                      GROUP BY raw_payload_hash
                      HAVING COUNT(*) > 1
                    ) s
                    """
                )
            ).scalar() or 0
            if hash_dupes > 0:
                _emit(
                    "ERROR",
                    f"raw_ingested_jobs duplicate raw_payload_hash groups: {hash_dupes:,}",
                    lines,
                )
            else:
                _emit("OK", "raw_ingested_jobs: no duplicate raw_payload_hash groups", lines)
        except Exception as exc:
            _emit("ERROR", f"raw_payload_hash duplicate check: {exc}", lines)
        print()

        # ----- 3e Q&A quality -----
        print("3e) Q&A quality")
        try:
            r = conn.execute(
                text(
                    """
                    SELECT seniority_level, COUNT(*) AS c
                    FROM dbo.job_postings
                    WHERE seniority_level IS NOT NULL
                    GROUP BY seniority_level
                    ORDER BY c DESC
                    LIMIT 1
                    """
                )
            ).first()
            nn = conn.execute(
                text("SELECT COUNT(*) FROM dbo.job_postings WHERE seniority_level IS NOT NULL")
            ).scalar() or 0
            if r and nn > 0:
                top_share = fraction(int(r[1]), nn)
                if top_share > SENIORITY_TOP_BUCKET_MAX_FRACTION:
                    _emit(
                        "WARN",
                        f"seniority_level dominance: top value {r[0]!r} holds {top_share:.1%} of non-null "
                        f"(max {SENIORITY_TOP_BUCKET_MAX_FRACTION:.0%})",
                        lines,
                    )
                else:
                    _emit("OK", f"seniority_level: top bucket share {top_share:.1%}", lines)
            else:
                _emit("WARN", "seniority_level: no non-null rows", lines)
        except Exception as exc:
            _emit("ERROR", f"seniority_level dominance: {exc}", lines)

        try:
            row = conn.execute(
                text(
                    """
                    SELECT
                      COUNT(*) AS total_jp,
                      COUNT(date_posted) AS nn,
                      COUNT(*) FILTER (
                        WHERE date_posted IS NOT NULL
                          AND (date_posted::timestamp)::date > CURRENT_DATE
                      ) AS future_nn
                    FROM dbo.job_postings
                    """
                )
            ).mappings().one()
            total_jp = int(row["total_jp"])
            nn = int(row["nn"])
            future_nn = int(row["future_nn"])
            null_share = fraction(total_jp - nn, total_jp)
            future_share = fraction(future_nn, nn) if nn else 0.0
            if null_share > DATE_POSTED_NULL_MAX_FRACTION:
                _emit(
                    "WARN",
                    f"date_posted NULL share {null_share:.1%} (max {DATE_POSTED_NULL_MAX_FRACTION:.0%})",
                    lines,
                )
            if nn > 0 and future_share > DATE_POSTED_FUTURE_MAX_FRACTION:
                _emit(
                    "WARN",
                    f"date_posted future share {future_share:.1%} of non-null (max "
                    f"{DATE_POSTED_FUTURE_MAX_FRACTION:.0%})",
                    lines,
                )
            if nn > 0:
                conc = conn.execute(
                    text(
                        """
                        SELECT MAX(cnt), SUM(cnt) FROM (
                          SELECT COUNT(*) AS cnt
                          FROM dbo.job_postings
                          WHERE date_posted IS NOT NULL
                          GROUP BY (date_posted::timestamp)::date
                        ) d
                        """
                    )
                ).first()
                if conc and conc[1]:
                    max_cnt, sum_cnt = int(conc[0]), int(conc[1])
                    day_share = fraction(max_cnt, sum_cnt)
                    if day_share > DATE_POSTED_SINGLE_DAY_CONCENTRATION_MAX:
                        _emit(
                            "WARN",
                            f"date_posted single-day concentration: one day holds {day_share:.1%} of non-null "
                            f"(max {DATE_POSTED_SINGLE_DAY_CONCENTRATION_MAX:.0%})",
                            lines,
                        )
                    else:
                        _emit("OK", f"date_posted: NULL {null_share:.1%}, future {future_share:.1%}", lines)
                else:
                    _emit("OK", f"date_posted: NULL {null_share:.1%}, future {future_share:.1%}", lines)
            else:
                _emit("WARN", "date_posted: all NULL", lines)
        except Exception as exc:
            _emit("ERROR", f"date_posted checks: {exc}", lines)

        try:
            inv_n = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM dbo.job_postings
                    WHERE salary_min IS NOT NULL AND salary_max IS NOT NULL
                      AND salary_min > salary_max
                    """
                )
            ).scalar() or 0
            if inv_n > 0:
                _emit("ERROR", f"salary inversion (salary_min > salary_max): {inv_n:,} row(s)", lines)
            else:
                _emit("OK", "salary: no min > max when both set", lines)
        except Exception as exc:
            _emit("ERROR", f"salary inversion check: {exc}", lines)

        try:
            total_jp = conn.execute(text("SELECT COUNT(*) FROM dbo.job_postings")).scalar() or 0
            ir_nn = conn.execute(text("SELECT COUNT(*) FROM dbo.job_postings WHERE is_remote IS NOT NULL")).scalar() or 0
            cov = fraction(ir_nn, total_jp) if total_jp else 0.0
            if total_jp and cov < IS_REMOTE_MIN_COVERAGE_FRACTION:
                _emit(
                    "WARN",
                    f"is_remote non-null coverage {cov:.1%} (min {IS_REMOTE_MIN_COVERAGE_FRACTION:.0%})",
                    lines,
                )
            else:
                _emit("OK", f"is_remote coverage {cov:.1%} of postings", lines)
        except Exception as exc:
            _emit("ERROR", f"is_remote coverage: {exc}", lines)
        print()

        # ----- 3f Schema -----
        print("3f) Schema: extensions, indexes, column types")
        try:
            have = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT extname FROM pg_extension "
                        "WHERE extname IN ('vector', 'uuid-ossp')"
                    )
                ).fetchall()
            }
            for ext in EXPECTED_EXTENSIONS:
                if ext in have:
                    _emit("OK", f"extension present: {ext}", lines)
                else:
                    _emit("WARN", f"extension missing: {ext}", lines)
        except Exception as exc:
            _emit("ERROR", f"pg_extension query: {exc}", lines)

        try:
            idx_rows = conn.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'dbo' AND tablename = 'job_postings'
                    """
                )
            ).fetchall()
            markers = [m for m in QNA_INDEX_COLUMN_MARKERS if any(m in (d or "") for _, d in idx_rows)]
            if markers:
                _emit(
                    "OK",
                    f"job_postings indexes referencing Q&A columns (smoke): {', '.join(markers)}",
                    lines,
                )
            else:
                _emit(
                    "WARN",
                    "job_postings: no indexdef mentioning Q&A column names — verify migrations",
                    lines,
                )
        except Exception as exc:
            _emit("WARN", f"pg_indexes job_postings: {exc}", lines)

        for col in ("job_posting_id", "dedup_embedding"):
            try:
                r = conn.execute(
                    text(
                        """
                        SELECT data_type, udt_name
                        FROM information_schema.columns
                        WHERE table_schema = 'dbo' AND table_name = 'job_postings'
                          AND column_name = :col
                        """
                    ),
                    {"col": col},
                ).first()
                if r is None:
                    _emit("ERROR", f"job_postings.{col}: column missing", lines)
                else:
                    dt, udt = str(r[0]).lower(), str(r[1]).lower()
                    ok = (
                        (dt == "uuid" or udt == "uuid")
                        if col == "job_posting_id"
                        else (udt == "vector" or "vector" in dt)
                    )
                    if ok:
                        _emit("OK", f"job_postings.{col}: data_type={r[0]!r} udt={r[1]!r}", lines)
                    else:
                        _emit(
                            "WARN",
                            f"job_postings.{col}: unexpected type data_type={r[0]!r} udt={r[1]!r}",
                            lines,
                        )
            except Exception as exc:
                _emit("ERROR", f"column type {col}: {exc}", lines)
        print()

        # ----- 3g Analytics tables -----
        print("3g) Analytics / orchestration")
        for tbl in ("canonical_roles", "disruption_fingerprints", "sector_summary_weekly"):
            try:
                reg = conn.execute(text("SELECT to_regclass(:q)"), {"q": f"dbo.{tbl}"}).scalar()
                if reg is None:
                    _emit("WARN", f"table {tbl}: not present (skipped empty check)", lines)
                    continue
                n = conn.execute(text(f"SELECT COUNT(*) FROM dbo.{tbl}")).scalar() or 0
                if n == 0:
                    _emit(
                        "WARN",
                        f"{tbl}: exists but 0 rows — analytics aggregates may be unfilled",
                        lines,
                    )
                else:
                    _emit("OK", f"{tbl}: {n:,} rows", lines)
            except Exception as exc:
                _emit("ERROR", f"{tbl}: {exc}", lines)

        try:
            ocnt = conn.execute(text("SELECT COUNT(*) FROM dbo.orchestration_audit_log")).scalar() or 0
            mx = conn.execute(text("SELECT MAX(created_at) FROM dbo.orchestration_audit_log")).scalar()
            _emit("OK", f"orchestration_audit_log: {ocnt:,} rows, MAX(created_at)={mx}", lines)
        except Exception as exc:
            _emit("ERROR", f"orchestration_audit_log: {exc}", lines)
        print()

        # ----- 3h Operational -----
        print("3h) Operational")
        try:
            v = conn.execute(text("SELECT version(), current_database()")).first()
            if v:
                print(f"     version: {v[0][:120]}...")
                print(f"     database: {v[1]}")
            _emit("OK", "SELECT version(), current_database()", lines)
        except Exception as exc:
            _emit("ERROR", f"version/current_database: {exc}", lines)

        try:
            for tbl in sorted(TABLE_COUNT_FLOORS.keys()):
                sz = conn.execute(
                    text("SELECT pg_total_relation_size(to_regclass(:q))"),
                    {"q": f"dbo.{tbl}"},
                ).scalar()
                if sz is None:
                    continue
                b = int(sz)
                if b > TABLE_SIZE_WARN_BYTES:
                    _emit(
                        "WARN",
                        f"table size dbo.{tbl}: {b:,} bytes (warn > {TABLE_SIZE_WARN_BYTES:,})",
                        lines,
                    )
        except Exception as exc:
            _emit("WARN", f"table size check: {exc}", lines)
        print()

    # --- Summary ---
    errors = [m for lvl, m in lines if lvl == "ERROR"]
    warns = [m for lvl, m in lines if lvl == "WARN"]
    print("--- Summary ---")
    print(f"  ERRORs: {len(errors)}")
    print(f"  WARNs:  {len(warns)}")
    if errors:
        print("\n  Remediation: fix ERRORs first — broken FKs, dupes, salary inversions, missing columns.")
        print("  Re-run migrations (python scripts/db_check.py migrate), re-seed if needed, backfill Q&A.")
        return 1
    if warns:
        print("\n  Remediation hints: review WARN lines — floors, #197 role buckets, analytics backfill, large tables.")
        print("  Often acceptable for partial local datasets; use team context.")
    else:
        print("\n  No ERROR or WARN — good for local Q&A / golden-question runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
