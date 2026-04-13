"""
Export PostgreSQL fixture data for local dev seeding.

Admin tool: run after database changes, commit updated fixtures to git so devs
can seed their local databases without running the LLM pipeline.

All output goes to scripts/pg-seed-data/fixtures/ — single source of truth.

Scopes
------
  reference  Export all non-PII dbo tables (reference data: skills, companies,
             taxonomies, AND agent pipeline tables)
  agent      Export agent pipeline tables only (raw_ingested_jobs, normalized_jobs,
             extracted_intelligence, etc.) — subset of reference, useful when only
             pipeline data has changed
  all        Both scopes (default — same as reference since both write to fixtures/)

Usage (from project root, with venv activated):
    python scripts/pg-seed-data/export_fixtures.py                    # all tables
    python scripts/pg-seed-data/export_fixtures.py --scope reference  # all tables
    python scripts/pg-seed-data/export_fixtures.py --scope agent      # pipeline tables only
    python scripts/pg-seed-data/export_fixtures.py --limit 500        # cap rows per table
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_REPO_ROOT / ".env")

import psycopg2  # noqa: E402

# ── Output directory ──────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
FIXTURES_DIR = SCRIPT_DIR / "fixtures"  # single output directory for all scopes

# ── Reference scope configuration ────────────────────────────────────

# Tables excluded from reference export (PII or agent-managed staging)
PII_TABLES: set[str] = {
    "users",
    "jobseekers",
    "jobseekers_private_data",
    "jobseekers_education",
    "jobseeker_has_skills",
    "certificates",
    "work_experiences",
    "project_experiences",
    "project_has_skills",
    "volunteer_has_skills",
    "account",
    "session",
    "authenticator",
    "verificationtoken",
    "employers",
    "educators",
    "volunteers",
    "cfa_admin",
    "bookmarked_jobseekers",
    "events_on_users",
    "vw_userjobseekers",
    "jobseekerjobposting",
    "jobseekerjobpostingskillmatch",
    "casemgmt",
    "casemgmtnotes",
    "meeting",
    "brandingrating",
    "careerprepassessment",
    "cybersecurityrating",
    "dataanalyticsrating",
    "durableskillsrating",
    "itcloudrating",
    "softwaredevrating",
    "jobplacement",
    "traineedetail",
    "employerjobrolefeedback",
    "_prisma_migrations",
}

REFERENCE_EXCLUDED = PII_TABLES

# FK columns referencing PII tables — NULL these in reference exports
PII_FK_COLUMNS: dict[str, list[str]] = {
    "companies": ["createdby"],
    "job_postings": ["employer_id"],
    "events": ["createdbyid"],
}

# Columns too large for git fixtures (pgvector embeddings) — excluded entirely
EXCLUDE_COLUMNS: dict[str, set[str]] = {
    "skills": {"embedding"},
}

# ── Agent scope configuration ─────────────────────────────────────────

# Agent pipeline tables in dependency order
AGENT_TABLES: list[str] = [
    "raw_ingested_jobs",
    "job_ingestion_runs",
    "normalized_jobs",
    "normalization_quarantine",
    "extracted_intelligence",
    "employer_profiles",
    "companies",
    "naics",
    "job_postings",
    "llm_audit_log",
]


# ── Shared helpers ────────────────────────────────────────────────────


def json_serializer(obj: object) -> str | float | None:
    """Custom JSON serializer for PostgreSQL types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj).upper()
    if isinstance(obj, (bytes, memoryview)):
        raw = bytes(obj) if isinstance(obj, memoryview) else obj
        return raw.decode("utf-8", errors="replace")
    raise TypeError(f"Not JSON serializable: {type(obj)}")


def get_pg_connection() -> psycopg2.extensions.connection:
    """Create psycopg2 connection from PYTHON_DATABASE_URL."""
    dsn = os.getenv("PYTHON_DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
    if not dsn:
        print("ERROR: Set PYTHON_DATABASE_URL in your .env file")
        sys.exit(1)
    return psycopg2.connect(dsn)


def get_dbo_tables(cur: psycopg2.extensions.cursor) -> list[str]:
    """Return all table names in the dbo schema, sorted."""
    cur.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'dbo'
        ORDER BY tablename
    """)
    return [row[0] for row in cur.fetchall()]


def get_columns(cur: psycopg2.extensions.cursor, table_name: str) -> list[tuple[str, str, str]]:
    """Return (column_name, data_type, udt_name) for a dbo table."""
    cur.execute(
        """
        SELECT column_name, data_type, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'dbo' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    return cur.fetchall()


def export_table(
    cur: psycopg2.extensions.cursor,
    table_name: str,
    columns: list[tuple[str, str, str]],
    *,
    null_pii_fks: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """Export one table to a list of row dicts."""
    excluded = EXCLUDE_COLUMNS.get(table_name, set())
    columns = [c for c in columns if c[0] not in excluded]

    select_parts: list[str] = []
    for col_name, _data_type, udt_name in columns:
        if udt_name == "vector":
            select_parts.append(f'"{col_name}"::text AS "{col_name}"')
        else:
            select_parts.append(f'"{col_name}"')

    query = f'SELECT {", ".join(select_parts)} FROM "dbo"."{table_name}"'
    if limit:
        query += f" LIMIT {limit}"
    cur.execute(query)
    rows = cur.fetchall()

    pii_fk_cols: set[str] = set()
    if null_pii_fks:
        pii_fk_cols = {c.lower() for c in PII_FK_COLUMNS.get(table_name, [])}

    records: list[dict] = []
    for row in rows:
        record: dict = {}
        for i, (col_name, _dt, _udt) in enumerate(columns):
            val = row[i]
            if col_name.lower() in pii_fk_cols:
                val = None
            record[col_name] = val
        records.append(record)

    return records


def write_json(filepath: Path, data: object) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, default=json_serializer, indent=2, ensure_ascii=False)
    filepath.write_text(text + "\n", encoding="utf-8")


# ── Scope exporters ───────────────────────────────────────────────────


def export_reference(cur: psycopg2.extensions.cursor, limit: int | None) -> dict:
    """Export all non-PII dbo tables to fixtures/."""
    print(f"\n{'='*60}")
    print("Reference Scope -> fixtures/")
    print(f"{'='*60}")

    all_tables = get_dbo_tables(cur)
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    metadata: dict = {
        "exportedAt": datetime.utcnow().isoformat() + "Z",
        "scope": "reference",
        "source": "postgresql",
        "schema": "dbo",
        "counts": {},
        "skipped": [],
    }

    for table in all_tables:
        if table.lower() in REFERENCE_EXCLUDED:
            metadata["skipped"].append(table)
            print(f"  {table}: SKIPPED")
            continue

        columns = get_columns(cur, table)
        if not columns:
            metadata["skipped"].append(table)
            print(f"  {table}: SKIPPED (no columns)")
            continue

        records = export_table(cur, table, columns, null_pii_fks=True, limit=limit)
        write_json(FIXTURES_DIR / f"{table}.json", records)
        metadata["counts"][table] = len(records)
        print(f"  {table}: {len(records):,} rows -> fixtures/{table}.json")

    write_json(FIXTURES_DIR / "metadata.json", metadata)
    return metadata


def export_agent(cur: psycopg2.extensions.cursor, limit: int | None) -> dict:
    """Export agent pipeline tables only to fixtures/."""
    print(f"\n{'='*60}")
    print("Agent Scope -> fixtures/")
    print(f"{'='*60}")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    metadata: dict = {
        "exportedAt": datetime.utcnow().isoformat() + "Z",
        "scope": "agent",
        "source": "postgresql",
        "schema": "dbo",
        "counts": {},
        "skipped": [],
    }

    for table in AGENT_TABLES:
        columns = get_columns(cur, table)
        if not columns:
            metadata["skipped"].append(table)
            print(f"  {table}: SKIPPED (table does not exist or no columns)")
            continue

        records = export_table(cur, table, columns, null_pii_fks=False, limit=limit)
        if not records:
            metadata["skipped"].append(table)
            print(f"  {table}: SKIPPED (0 rows)")
            continue

        write_json(FIXTURES_DIR / f"{table}.json", records)
        metadata["counts"][table] = len(records)
        print(f"  {table}: {len(records):,} rows -> fixtures/{table}.json")

    write_json(FIXTURES_DIR / "metadata.json", metadata)
    return metadata


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export PostgreSQL fixture data for local dev seeding."
    )
    parser.add_argument(
        "--scope",
        choices=["reference", "agent", "all"],
        default="all",
        help="reference=non-PII dbo tables, agent=pipeline tables, all=both (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Cap rows exported per table (useful for large pipeline tables)",
    )
    args = parser.parse_args()

    conn = get_pg_connection()
    cur = conn.cursor()

    ref_meta: dict | None = None
    agent_meta: dict | None = None

    if args.scope in ("reference", "all"):
        ref_meta = export_reference(cur, args.limit)

    if args.scope in ("agent", "all"):
        agent_meta = export_agent(cur, args.limit)

    cur.close()
    conn.close()

    print(f"\n{'='*60}")
    if ref_meta:
        ref_rows = sum(ref_meta["counts"].values())
        print(f"Reference: {ref_rows:,} rows across {len(ref_meta['counts'])} tables -> fixtures/")
    if agent_meta:
        agent_rows = sum(agent_meta["counts"].values())
        print(f"Agent:     {agent_rows:,} rows across {len(agent_meta['counts'])} tables -> fixtures/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
