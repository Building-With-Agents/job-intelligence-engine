"""
Seed agent pipeline data (enriched jobs, companies, NAICS) from JSON fixtures.

Called automatically by seed_pg_database.py. Can also be run standalone.
Idempotent: existing rows are preserved. For tables in UPSERT_UPDATE_COLUMNS,
new columns added by recent migrations get filled from the fixture via
COALESCE — so re-seeding after a schema change populates new columns on
existing rows without clobbering any locally-populated state.

Usage (from project root, with venv activated):
    python scripts/pg-seed-data/seed_agent_data.py

Reads:  scripts/pg-seed-data/fixtures/*.json  (data)
Writes: PostgreSQL database specified by PYTHON_DATABASE_URL
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load .env from repo root (two levels up from this script)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_REPO_ROOT / ".env")

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

# ── Paths ─────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ── UPSERT override map ───────────────────────────────────────────────
# Tables listed here use ON CONFLICT DO UPDATE SET col = COALESCE(target.col, EXCLUDED.col)
# instead of ON CONFLICT DO NOTHING. The COALESCE pattern means:
#   - If existing row's column is NULL → set it from the fixture value
#   - If existing row's column has a value → keep the existing value (no clobber)
#
# Use this for columns added by recent migrations: dev DBs that already have
# the rows but lack the new column data get those columns filled in by re-seeding,
# without losing any state that was locally populated.
#
# Columns not present in the fixture are silently skipped (backward compatible
# with older fixtures that don't yet have the column).
UPSERT_UPDATE_COLUMNS: dict[str, list[str]] = {
    "job_postings": [
        # Week 8 (#170) Q&A-ready promotions
        "date_posted",
        "seniority_level",
        "is_remote",
        # Week 8 (#173) role_classification promotion
        "role_classification",
        # Week 8 (#174) structured salary promotions
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
    ],
    # Week 9 (#253): canonical_roles upsert so partial/stale rows get refreshed
    # from the fixture without clobbering locally-populated state. COALESCE
    # semantics: if the existing column is NULL, take fixture value; otherwise
    # keep existing. PK columns (id, role_id) are conflict targets, not updated.
    # created_at omitted deliberately — immutable on existing rows.
    "canonical_roles": [
        "label",
        "description",
        "posting_count",
        "cluster_centroid",
        "representative_titles",
        "top_skills",
        "top_tools",
        "is_llm_generated",
        "computed_at",
        "updated_at",
    ],
}

# ── FK-safe insert order ──────────────────────────────────────────────
# Tables ordered so that FK dependencies are satisfied:
# companies has no FK deps — must be before job_postings (company_id FK)
# naics has no FK deps — reference table for NAICS codes
# canonical_roles has no FK deps on other agent tables — must be before
#   job_postings (canonical_role_id FK, referenced by ~55% of rows)
# raw_ingested_jobs has no FK deps on other agent tables
# job_ingestion_runs has no FK deps on other agent tables
# normalized_jobs → raw_ingested_jobs (via raw_ingested_job_id)
# extracted_intelligence → normalized_jobs (via normalized_job_id)
# employer_profiles has no FK deps on other agent tables
# job_postings → companies (via company_id), canonical_roles (via canonical_role_id)
# llm_audit_log has no FK deps on other agent tables

INSERT_ORDER = [
    "companies",
    "naics",
    "canonical_roles",
    "raw_ingested_jobs",
    "job_ingestion_runs",
    "normalized_jobs",
    "normalization_quarantine",
    "extracted_intelligence",
    "employer_profiles",
    "job_postings",
    "llm_audit_log",
]


def get_pg_connection() -> psycopg2.extensions.connection:
    """Create psycopg2 connection from PYTHON_DATABASE_URL."""
    dsn = os.getenv("PYTHON_DATABASE_URL", "")
    dsn = dsn.replace("postgresql+psycopg2://", "postgresql://")
    if not dsn:
        print("ERROR: Set PYTHON_DATABASE_URL in your .env file")
        sys.exit(1)
    return psycopg2.connect(dsn, connect_timeout=30, options="-c statement_timeout=300000")


def get_primary_key(cur: psycopg2.extensions.cursor, table: str) -> list[str]:
    """Get primary key column(s) for a dbo table."""
    cur.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE i.indisprimary
          AND c.relname = %s
          AND n.nspname = 'dbo'
        ORDER BY array_position(i.indkey, a.attnum)
    """,
        (table,),
    )
    return [row[0] for row in cur.fetchall()]


def _convert_value(val):
    """Convert a fixture value for PostgreSQL insertion."""
    # Convert ISO datetime strings back to datetime objects
    if isinstance(val, str) and len(val) >= 19:
        for _fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.fromisoformat(val)
            except ValueError:
                continue
    # Handle JSON/dict values — store as JSON string
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return val


def _build_on_conflict_clause(table: str, pk_cols: list[str], columns: list[str]) -> str:
    """Build the ON CONFLICT clause for the upsert.

    For tables in UPSERT_UPDATE_COLUMNS, filter the configured update columns
    to those present in the fixture, then build:
        ON CONFLICT (pk) DO UPDATE SET col = COALESCE("dbo"."<table>".col, EXCLUDED.col), ...
    Otherwise:
        ON CONFLICT (pk) DO NOTHING
    """
    conflict_cols = ", ".join(f'"{c}"' for c in pk_cols)
    update_cols = [c for c in UPSERT_UPDATE_COLUMNS.get(table, []) if c in columns]
    if not update_cols:
        return f"ON CONFLICT ({conflict_cols}) DO NOTHING"
    set_clauses = ", ".join(f'"{c}" = COALESCE("dbo"."{table}"."{c}", EXCLUDED."{c}")' for c in update_cols)
    return f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {set_clauses}"


def upsert_records(
    cur: psycopg2.extensions.cursor,
    table: str,
    records: list[dict],
    pk_cols: list[str],
) -> tuple[int, int]:
    """Insert records with ON CONFLICT handling using fast batch inserts.

    For tables in UPSERT_UPDATE_COLUMNS, conflicts trigger a COALESCE-based
    UPDATE that fills NULL columns from the fixture without clobbering existing
    values. For all other tables, conflicts are skipped (DO NOTHING).

    Returns (inserted_or_updated, skipped). Uses psycopg2.extras.execute_values()
    for batched network round-trips instead of row-by-row.
    """
    if not records:
        return 0, 0

    columns = list(records[0].keys())
    col_names = ", ".join(f'"{c}"' for c in columns)
    on_conflict = _build_on_conflict_clause(table, pk_cols, columns)

    insert_sql = f'INSERT INTO "dbo"."{table}" ({col_names}) VALUES %s {on_conflict}'

    # Pre-convert all values into tuple list
    values_list = []
    for record in records:
        values_list.append(tuple(_convert_value(record.get(c)) for c in columns))

    try:
        psycopg2.extras.execute_values(
            cur,
            insert_sql,
            values_list,
            page_size=1000,
        )
        # rowcount counts both INSERTed and UPDATEd rows under DO UPDATE; under
        # DO NOTHING it counts only INSERTed. "skipped" = total - touched.
        touched = cur.rowcount if cur.rowcount >= 0 else len(values_list)
        skipped = len(values_list) - touched
        return touched, skipped
    except Exception as exc:
        cur.connection.rollback()
        print(f"    BATCH ERROR: {str(exc)[:300]}")
        # Fall back to row-by-row to identify problematic rows
        placeholders = ", ".join(["%s"] * len(columns))
        row_sql = f'INSERT INTO "dbo"."{table}" ({col_names}) VALUES ({placeholders}) {on_conflict}'
        touched = 0
        for i, vals in enumerate(values_list):
            try:
                cur.execute(row_sql, vals)
                cur.connection.commit()
                if cur.rowcount > 0:
                    touched += 1
            except Exception as row_exc:
                cur.connection.rollback()
                if i < 3:
                    print(f"    Row {i + 1} error: {str(row_exc)[:200]}")
        return touched, len(values_list) - touched


def run_migrations() -> None:
    """Run agent migrations to ensure schema is current."""
    try:
        from common.data_store.database import get_engine
        from common.data_store.migrations import run_migrations as _migrate

        _migrate(get_engine())
        print("Migrations: OK")
    except Exception as e:
        print(f"Migrations: SKIPPED ({e})")
        print("  (Tables may already exist — continuing with seed)")


def seed_all() -> None:
    """Seed all agent pipeline tables from JSON fixtures."""
    print("=" * 60)
    print("Agent Pipeline Data Seed")
    print("=" * 60)

    if not FIXTURES_DIR.exists():
        print(f"\nERROR: Fixtures directory not found: {FIXTURES_DIR}")
        print("Run export_fixtures.py first: python scripts/pg-seed-data/export_fixtures.py --scope agent")
        sys.exit(1)

    # Run migrations first
    print("\nRunning migrations...")
    run_migrations()

    conn = get_pg_connection()
    cur = conn.cursor()

    total_inserted = 0
    total_skipped = 0

    print(f"\nSeeding from: {FIXTURES_DIR}\n")

    for table in INSERT_ORDER:
        fixture_file = FIXTURES_DIR / f"{table}.json"
        if not fixture_file.exists():
            print(f"  {table}: SKIPPED (no fixture file)")
            continue

        records = json.loads(fixture_file.read_text(encoding="utf-8"))
        if not records:
            print(f"  {table}: SKIPPED (0 records in fixture)")
            continue

        pk_cols = get_primary_key(cur, table)
        if not pk_cols:
            print(f"  {table}: SKIPPED (no primary key found — table may not exist)")
            conn.rollback()
            continue

        try:
            inserted, skipped = upsert_records(cur, table, records, pk_cols)
            conn.commit()
            total_inserted += inserted
            total_skipped += skipped
            print(f"  {table}: {inserted:,} inserted, {skipped:,} skipped (of {len(records):,} total)")
        except Exception as e:
            conn.rollback()
            print(f"  {table}: ERROR — {e}")

    print(f"\n{'=' * 60}")
    print(f"Seed complete: {total_inserted:,} inserted, {total_skipped:,} skipped")
    print("=" * 60)

    cur.close()
    conn.close()


if __name__ == "__main__":
    seed_all()
