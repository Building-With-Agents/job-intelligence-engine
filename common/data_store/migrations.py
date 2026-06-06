"""Idempotent database migrations for all agent-managed tables.

SQLAlchemy is the single database authority. Creates agent tables, adds
enrichment columns to job_postings, and ensures reference tables are
accessible.

Optional legacy raw-SQL scripts (pre-consolidation paths) live under
``legacy_migrations/`` for reference only; use :func:`run_migrations` for the app.

Usage:
    from common.data_store.migrations import run_migrations
    from common.data_store.database import get_engine
    run_migrations(get_engine())
    # Or, when ``PYTHON_DATABASE_URL`` is set:
    run_migrations()
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.engine import Engine

from common.data_store.models import Base

log = structlog.get_logger()

# ``job_postings`` is pipeline-owned and not declared on ``Base`` (legacy Prisma shape).
# ``create_all`` therefore skips it; this shell is created once so ALTER / index steps apply.
_JOB_POSTINGS_SHELL_DDL = """
CREATE TABLE IF NOT EXISTS dbo.job_postings (
    job_posting_id TEXT PRIMARY KEY,
    company_id TEXT,
    tech_area_id TEXT,
    sector_id TEXT,
    job_title TEXT NOT NULL DEFAULT '',
    job_description TEXT,
    employment_type TEXT,
    location TEXT,
    salary_range TEXT,
    county TEXT,
    zip TEXT,
    publish_date TIMESTAMPTZ,
    unpublish_date TIMESTAMPTZ,
    job_post_url TEXT,
    occupation_code TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    location_id TEXT,
    createdat TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updatedat TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _ensure_job_postings_shell(engine: Engine) -> None:
    """Create empty ``dbo.job_postings`` when missing (PostgreSQL only)."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'dbo' AND table_name = 'job_postings' LIMIT 1"
            )
        ).scalar()
        if exists:
            return
        conn.execute(text(_JOB_POSTINGS_SHELL_DDL))
    log.info("migrations_job_postings_shell_created")


def _drop_legacy_employer_profiles_if_serial_pk(engine: Engine) -> None:
    """Replace pre-UUID ``employer_profiles`` (SERIAL id) so ORM/create_all can recreate."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'dbo' AND table_name = 'employer_profiles'
                )
                """
            )
        ).scalar()
        if not exists:
            return
        row = conn.execute(
            text(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = 'dbo' AND table_name = 'employer_profiles'
                  AND column_name = 'id'
                """
            )
        ).first()
        if row and row[0] in ("integer", "bigint", "smallint"):
            conn.execute(text("DROP TABLE IF EXISTS dbo.employer_profiles CASCADE"))
            log.info("migrations_employer_profiles_legacy_serial_dropped")


_EXTRACTED_INTELLIGENCE_DDL = """
CREATE TABLE IF NOT EXISTS dbo.extracted_intelligence (
    id SERIAL PRIMARY KEY,
    normalized_job_id INTEGER,
    extraction_version TEXT NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extraction_model TEXT NOT NULL,
    extraction_tokens_used INTEGER NOT NULL DEFAULT 0,
    extraction_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    skills JSONB NOT NULL DEFAULT '[]',
    tools JSONB NOT NULL DEFAULT '[]',
    tasks JSONB NOT NULL DEFAULT '[]',
    responsibilities JSONB NOT NULL DEFAULT '[]',
    context JSONB NOT NULL DEFAULT '[]',
    overall_confidence DOUBLE PRECISION,
    extraction_warnings JSONB DEFAULT '[]',
    extraction_failed BOOLEAN NOT NULL DEFAULT FALSE,
    extraction_metadata JSONB
);
CREATE INDEX IF NOT EXISTS ix_extracted_intelligence_normalized_job_id
    ON dbo.extracted_intelligence (normalized_job_id);
CREATE INDEX IF NOT EXISTS ix_extracted_intelligence_extracted_at
    ON dbo.extracted_intelligence (extracted_at);
CREATE INDEX IF NOT EXISTS ix_extracted_intelligence_failed
    ON dbo.extracted_intelligence (extraction_failed);
"""

_LLM_AUDIT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS dbo.llm_audit_log (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(100) NOT NULL,
    prompt_hash VARCHAR(64) NOT NULL,
    model VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    token_count INTEGER,
    cost_usd DOUBLE PRECISION,
    success BOOLEAN NOT NULL,
    error_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_llm_audit_log_agent_name ON dbo.llm_audit_log (agent_name);
CREATE INDEX IF NOT EXISTS ix_llm_audit_log_created_at ON dbo.llm_audit_log (created_at);
CREATE INDEX IF NOT EXISTS ix_llm_audit_log_success ON dbo.llm_audit_log (success);
"""

# Enrichment columns on dbo.job_postings (idempotent via IF NOT EXISTS).
# Phase 1 (Weeks 3-4): ingestion + extraction metadata.
# Phase 1b (Week 5): enrichment output — SOC, NAICS, temporal, borderplex, dedup.
_JOB_POSTINGS_ALTER_STATEMENTS = [
    # Phase 1 — ingestion & extraction metadata
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS source TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS external_id TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS ingestion_run_id TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS ai_relevance_score DOUBLE PRECISION",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS is_spam BOOLEAN",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS spam_score DOUBLE PRECISION",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS spam_tier TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS overall_confidence DOUBLE PRECISION",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS field_confidence JSONB",
    # Phase 1b — Week 5 enrichment output (SOC persisted here; legacy Prisma column may be occupation_code)
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS soc_code TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS naics_code TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS temporal_period TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS borderplex_subregion TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN DEFAULT FALSE",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS duplicate_cluster_id UUID",
    # Fuzzy dedup (IMP-018): cached embedding + content hash for same-company window search
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS dedup_text_hash TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS dedup_embedding vector(1536)",
    # Zip code (flywheel #161): resolved during normalization from posting or postal_geo_data lookup
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS zip_code VARCHAR(10)",
    # Link to dbo.employer_profiles (UUID PK) after enrichment upsert
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS employer_profile_id UUID REFERENCES dbo.employer_profiles(id)",
    # Week 7 — canonical role clustering (Pair C): posting → discovered role
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS canonical_role_id TEXT",
    # Week 8 (#170) — Q&A-ready fields: date_posted promoted from normalized_jobs,
    # seniority_level + is_remote promoted from RecordEnriched event payload.
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS date_posted TIMESTAMPTZ",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS seniority_level TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS is_remote BOOLEAN",
    # Week 8 (#173) — role_classification promoted from RecordEnriched event payload
    # (computed by classify_job() during enrichment; previously only used for sector_id resolution).
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS role_classification TEXT",
    # Week 8 (#174) — structured salary columns promoted from normalized_jobs.
    # Keep salary_range TEXT for backward compat; prefer structured columns for analytics.
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS salary_min NUMERIC",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS salary_max NUMERIC",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS salary_currency TEXT",
    "ALTER TABLE dbo.job_postings ADD COLUMN IF NOT EXISTS salary_period TEXT",
]

# Optional FK after ``canonical_roles`` exists (create_all + alters). Idempotent via try/except.
_CANONICAL_ROLE_JOB_POSTING_FK = """
ALTER TABLE dbo.job_postings
    ADD CONSTRAINT fk_job_postings_canonical_roles
    FOREIGN KEY (canonical_role_id) REFERENCES dbo.canonical_roles (role_id)
"""

# Legacy Prisma cleanup: drop FK constraints and make NOT NULL columns nullable (#159).
# Pipeline stores location directly on job_postings row, not via company_addresses FK.
_JOB_POSTINGS_LEGACY_CLEANUP = [
    # Drop FK to company_addresses — pipeline stores location directly on job_postings
    "ALTER TABLE dbo.job_postings DROP CONSTRAINT IF EXISTS fk_job_postings_company_addresses1",
    # Drop FK to companies — pipeline resolves company_id via _resolve_or_create_company
    "ALTER TABLE dbo.job_postings DROP CONSTRAINT IF EXISTS fk_job_postings_companies1",
    # Drop FK to employers / technology_areas — columns are 99–100% NULL; indexes are dead weight.
    # #170 deprecation: employer_id and tech_area_id are never written by the pipeline.
    "ALTER TABLE dbo.job_postings DROP CONSTRAINT IF EXISTS fk_job_postings_employers1",
    "ALTER TABLE dbo.job_postings DROP CONSTRAINT IF EXISTS fk_job_postings_technology_areas1",
    "DROP INDEX IF EXISTS dbo.fk_job_postings_employers1_idx",
    "DROP INDEX IF EXISTS dbo.fk_job_postings_technology_areas1_idx",
    "DROP INDEX IF EXISTS dbo.fk_job_postings_company_addresses1_idx",
    # Make legacy NOT NULL columns nullable
    "ALTER TABLE dbo.job_postings ALTER COLUMN location_id DROP NOT NULL",
    "ALTER TABLE dbo.job_postings ALTER COLUMN county DROP NOT NULL",
    "ALTER TABLE dbo.job_postings ALTER COLUMN zip DROP NOT NULL",
    "ALTER TABLE dbo.job_postings ALTER COLUMN publish_date DROP NOT NULL",
    "ALTER TABLE dbo.job_postings ALTER COLUMN unpublish_date DROP NOT NULL",
]

_NORMALIZED_JOBS_ALTER_STATEMENTS = [
    "ALTER TABLE dbo.normalized_jobs ADD COLUMN IF NOT EXISTS requirements TEXT",
    "ALTER TABLE dbo.normalized_jobs ADD COLUMN IF NOT EXISTS responsibilities TEXT",
    "ALTER TABLE dbo.normalized_jobs ADD COLUMN IF NOT EXISTS zip_code VARCHAR(10)",
    "ALTER TABLE dbo.raw_ingested_jobs ADD COLUMN IF NOT EXISTS zip_code VARCHAR(10)",
    "ALTER TABLE dbo.normalized_jobs ADD COLUMN IF NOT EXISTS naics_code TEXT",
    "ALTER TABLE dbo.normalized_jobs ADD COLUMN IF NOT EXISTS employer_metadata JSONB",
    # JIE #289 — promotion audit column. Stamped by apply_enrichment_to_job_postings
    # on successful promotion to dbo.job_postings. NULL means the row has not been
    # promoted yet; the sweeper at scripts/sweep_unpromoted_normalized_jobs.py picks
    # NULL rows up after a 1-hour grace window and re-attempts via the standard
    # promotion path. The grace window is also indexed for the sweeper's hot path.
    "ALTER TABLE dbo.normalized_jobs ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS ix_normalized_jobs_promoted_at_null ON dbo.normalized_jobs (created_at) WHERE promoted_at IS NULL",
]

# Company HQ / location fields for enrichment resolve_location (#110)
_COMPANIES_LOCATION_ALTER_STATEMENTS = [
    "ALTER TABLE dbo.companies ADD COLUMN IF NOT EXISTS city TEXT",
    "ALTER TABLE dbo.companies ADD COLUMN IF NOT EXISTS state TEXT",
    "ALTER TABLE dbo.companies ADD COLUMN IF NOT EXISTS normalized_location TEXT",
]

_UUID_REGEX = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# Backfill token columns when llm_audit_log predates full DDL (idempotent)
_LLM_AUDIT_LOG_ALTER_STATEMENTS = [
    "ALTER TABLE dbo.llm_audit_log ADD COLUMN IF NOT EXISTS input_tokens INTEGER",
    "ALTER TABLE dbo.llm_audit_log ADD COLUMN IF NOT EXISTS output_tokens INTEGER",
    "ALTER TABLE dbo.llm_audit_log ADD COLUMN IF NOT EXISTS token_count INTEGER",
]

# extracted_intelligence created before ExtractionMetadata JSONB column existed
_EXTRACTED_INTELLIGENCE_ALTER_STATEMENTS = [
    "ALTER TABLE dbo.extracted_intelligence ADD COLUMN IF NOT EXISTS extraction_metadata JSONB",
]

# NAICS reference table (PostgreSQL).
_NAICS_DDL = """
CREATE TABLE IF NOT EXISTS dbo.naics (
    naics_code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    seq_no INTEGER,
    createdat TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updatedat TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# Analytics Week 7 Pair D — posting_freshness + trajectory_map (ORM-aligned; idempotent with create_all).
_POSTING_FRESHNESS_DDL = """
CREATE TABLE IF NOT EXISTS dbo.posting_freshness (
    posting_id TEXT PRIMARY KEY,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    duration_days INTEGER NOT NULL,
    is_repost BOOLEAN NOT NULL DEFAULT FALSE,
    repost_count INTEGER NOT NULL DEFAULT 0,
    fill_proxy BOOLEAN NOT NULL DEFAULT FALSE,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_posting_freshness_posting_id
    ON dbo.posting_freshness (posting_id);
"""

_TRAJECTORY_MAP_DDL = """
CREATE TABLE IF NOT EXISTS dbo.trajectory_map (
    role_id TEXT PRIMARY KEY,
    trajectory_data JSONB,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_DISRUPTION_FINGERPRINTS_DDL = """
CREATE TABLE IF NOT EXISTS dbo.disruption_fingerprints (
    canonical_role_id TEXT PRIMARY KEY,
    disruption_category JSONB NOT NULL DEFAULT '[]'::jsonb,
    disruption_intensity DOUBLE PRECISION NOT NULL DEFAULT 0,
    skill_velocity JSONB NOT NULL DEFAULT '[]'::jsonb,
    tool_transition JSONB NOT NULL DEFAULT '[]'::jsonb,
    task_shift JSONB NOT NULL DEFAULT '[]'::jsonb,
    responsibility_expansion DOUBLE PRECISION NOT NULL DEFAULT 0,
    ai_intensity_trend TEXT NOT NULL DEFAULT 'stable',
    workflow_restructuring_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    trajectory TEXT NOT NULL DEFAULT 'stable',
    period_comparison JSONB NOT NULL DEFAULT '[]'::jsonb,
    content_fingerprint TEXT NOT NULL DEFAULT '',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_disruption_fingerprints_computed_at
    ON dbo.disruption_fingerprints (computed_at);
"""

# sector_summary_weekly schema upgrades (Step 6 — employer_count, top_skills, computed_at)
_SECTOR_SUMMARY_WEEKLY_ALTER_STATEMENTS = [
    "ALTER TABLE dbo.sector_summary_weekly ADD COLUMN IF NOT EXISTS employer_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE dbo.sector_summary_weekly ADD COLUMN IF NOT EXISTS top_skills JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE dbo.sector_summary_weekly ADD COLUMN IF NOT EXISTS computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "ALTER TABLE dbo.sector_summary_weekly DROP COLUMN IF EXISTS median_salary",
]

# Week 7 Pair B — weekly analytics aggregates (issues #180 / #181)
_WEEK7_ANALYTICS_AGGREGATES_DDL = """
CREATE TABLE IF NOT EXISTS dbo.sector_summary_weekly (
    id SERIAL PRIMARY KEY,
    week_start DATE NOT NULL,
    sector TEXT NOT NULL,
    posting_count INTEGER NOT NULL,
    employer_count INTEGER NOT NULL DEFAULT 0,
    avg_salary DOUBLE PRECISION,
    top_skills JSONB NOT NULL DEFAULT '[]'::jsonb,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS dbo.geo_demand_weekly (
    id SERIAL PRIMARY KEY,
    week_start DATE NOT NULL,
    borderplex_subregion VARCHAR(32) NOT NULL,
    posting_count INTEGER NOT NULL
);
"""

# Week 7: skill_demand_weekly may predate employer_count (IMP-021 distinct-employer metric)
_SKILL_DEMAND_WEEKLY_ALTER_STATEMENTS = [
    "ALTER TABLE dbo.skill_demand_weekly ADD COLUMN IF NOT EXISTS employer_count INTEGER NOT NULL DEFAULT 0",
]

# Week 10 (#229): label_embedding for pgvector role resolution in the Q&A router.
# Not ORM-mapped — follows the same unmapped-vector pattern as dedup_embedding on job_postings.
_CANONICAL_ROLES_ALTER_STATEMENTS = [
    "ALTER TABLE dbo.canonical_roles ADD COLUMN IF NOT EXISTS label_embedding vector(1536)",
]

# Issue #157: JSearch Pro-plan monthly budget counter — one column on job_ingestion_runs
_JOB_INGESTION_RUNS_ALTER_STATEMENTS = [
    "ALTER TABLE dbo.job_ingestion_runs ADD COLUMN IF NOT EXISTS api_requests_used INTEGER NOT NULL DEFAULT 0",
]
# Week 8 (#176) — Q&A retrieval indexes on job_postings and extracted_intelligence.
# All statements are idempotent (CREATE INDEX IF NOT EXISTS).
# Must be applied AFTER the #170 ADD COLUMN statements so date_posted and
# seniority_level exist on job_postings before the index is created.
_QNA_RETRIEVAL_INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS ix_job_postings_created_at ON dbo.job_postings (createdat DESC)",
    "CREATE INDEX IF NOT EXISTS ix_job_postings_date_posted ON dbo.job_postings (date_posted DESC)",
    "CREATE INDEX IF NOT EXISTS ix_job_postings_canonical_role ON dbo.job_postings (canonical_role_id)",
    "CREATE INDEX IF NOT EXISTS ix_job_postings_employer_profile ON dbo.job_postings (employer_profile_id)",
    "CREATE INDEX IF NOT EXISTS ix_job_postings_seniority ON dbo.job_postings (seniority_level)",
    "CREATE INDEX IF NOT EXISTS ix_job_postings_role_classification ON dbo.job_postings (role_classification)",
    "CREATE INDEX IF NOT EXISTS ix_job_postings_source_external ON dbo.job_postings (source, external_id)",
    # NOTE: ix_extracted_intelligence_src_ext was removed — extracted_intelligence has
    # no source/external_id columns (it links via normalized_job_id FK). The join path
    # is already covered by ix_normalized_jobs_source_eid on dbo.normalized_jobs.
]

_SERIAL_SEQUENCE_TARGETS = (
    ("raw_ingested_jobs", "id"),
    ("job_ingestion_runs", "id"),
    ("normalized_jobs", "id"),
    ("normalization_quarantine", "id"),
    ("extracted_intelligence", "id"),
    ("llm_audit_log", "id"),
    ("employer_profiles", "id"),
    ("cohort_gap_cache", "id"),
    ("orchestration_audit_log", "id"),
)


def _sync_serial_sequence(engine: Engine, *, table_name: str, column_name: str = "id") -> None:
    """Advance a PostgreSQL serial/identity sequence to at least ``MAX(column)``."""
    try:
        with engine.begin() as conn:
            seq_name = conn.execute(
                text("SELECT pg_get_serial_sequence(CAST(:table_name AS text), CAST(:column_name AS text))"),
                {"table_name": f"dbo.{table_name}", "column_name": column_name},
            ).scalar()
            if not seq_name:
                return

            max_value = conn.execute(text(f'SELECT COALESCE(MAX("{column_name}"), 0) FROM dbo.{table_name}')).scalar()
            max_value = int(max_value or 0)
            if max_value > 0:
                conn.execute(
                    text("SELECT setval(CAST(:seq AS regclass), :v, true)"),
                    {"seq": seq_name, "v": max_value},
                )
            else:
                conn.execute(
                    text("SELECT setval(CAST(:seq AS regclass), 1, false)"),
                    {"seq": seq_name},
                )
    except Exception as exc:
        log.warning(
            "migration_sequence_sync_skipped",
            table_name=table_name,
            column_name=column_name,
            error=str(exc),
        )


def _sync_agent_serial_sequences(engine: Engine) -> None:
    for table_name, column_name in _SERIAL_SEQUENCE_TARGETS:
        _sync_serial_sequence(engine, table_name=table_name, column_name=column_name)


def _ensure_duplicate_cluster_id_uuid(engine: Engine) -> None:
    """Convert ``job_postings.duplicate_cluster_id`` to ``UUID`` when legacy text remains."""
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'dbo'
                      AND table_name = 'job_postings'
                      AND column_name = 'duplicate_cluster_id'
                    """
                )
            ).first()
            if not row:
                return
            data_type = str(row[0] or "").lower()
            if data_type == "uuid":
                return

            invalid_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM dbo.job_postings
                    WHERE duplicate_cluster_id IS NOT NULL
                      AND BTRIM(duplicate_cluster_id::text) <> ''
                      AND NOT (duplicate_cluster_id::text ~* :uuid_regex)
                    """
                ),
                {"uuid_regex": _UUID_REGEX},
            ).scalar()
            invalid_count = int(invalid_count or 0)
            if invalid_count:
                log.warning(
                    "migration_duplicate_cluster_id_invalid_values_reset_to_null",
                    invalid_count=invalid_count,
                )

            conn.execute(
                text(
                    """
                    ALTER TABLE dbo.job_postings
                    ALTER COLUMN duplicate_cluster_id TYPE UUID
                    USING CASE
                        WHEN duplicate_cluster_id IS NULL THEN NULL
                        WHEN BTRIM(duplicate_cluster_id::text) = '' THEN NULL
                        WHEN duplicate_cluster_id::text ~* :uuid_regex THEN duplicate_cluster_id::uuid
                        ELSE NULL
                    END
                    """
                ),
                {"uuid_regex": _UUID_REGEX},
            )
    except Exception as exc:
        log.warning(
            "migration_duplicate_cluster_id_uuid_skipped",
            error=str(exc),
        )


# JIE #359 — curriculum path + normalized↔posting joins. Each runs in AUTOCOMMIT because
# ``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction block (PostgreSQL).
_CURRICULUM_CONCURRENT_INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_canonical_roles_label ON dbo.canonical_roles (label)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_canonical_roles_description ON dbo.canonical_roles (description)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_normalized_jobs_source_eid ON dbo.normalized_jobs (source, external_id)",
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_job_postings_source_eid ON dbo.job_postings (source, external_id)",
)


def _apply_curriculum_concurrent_indexes(engine: Engine) -> None:
    """Create non-blocking btree indexes for curriculum role match + (source, external_id) joins."""
    if engine.dialect.name != "postgresql":
        return
    for stmt in _CURRICULUM_CONCURRENT_INDEX_STATEMENTS:
        try:
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text(stmt))
            log.info("migration_concurrent_index_applied", statement=stmt[:120])
        except Exception as exc:
            log.warning(
                "migration_concurrent_index_skipped",
                statement=stmt[:120],
                error=str(exc),
            )


def run_migrations(engine: Engine | None = None) -> None:
    """Create agent tables and add Phase 1 columns. Safe to run multiple times.

    When ``engine`` is omitted, uses :func:`common.data_store.database.get_engine`
    (requires ``PYTHON_DATABASE_URL``).
    """
    if engine is None:
        from common.data_store.database import get_engine

        engine = get_engine()
    log.info("migrations_start")

    # 0. Ensure the dbo schema exists (required by ORM models)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS dbo"))

    # 0b. Drop legacy SERIAL-key employer_profiles before create_all (PostgreSQL only)
    _drop_legacy_employer_profiles_if_serial_pk(engine)

    # 1. Create agent-managed tables via SQLAlchemy metadata
    Base.metadata.create_all(engine)
    log.info("migrations_tables_created")

    # 1a. ``job_postings`` is not on ``Base`` — ensure table exists before ALTER / indexes.
    _ensure_job_postings_shell(engine)

    # 1b. Seed analytics pipeline state singleton (id=1) for DB-backed watermark
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO dbo.analytics_pipeline_state (id, last_successful_run_at, updated_at)
                        VALUES (1, NULL, NOW())
                        ON CONFLICT (id) DO NOTHING
                        """
                    )
                )
            log.info("migrations_analytics_pipeline_state_seeded")
        except Exception as exc:
            log.warning(
                "migration_analytics_pipeline_state_seed_skipped",
                error=str(exc),
            )

    # 2. Create extracted_intelligence table (DDL may add indexes idempotently)
    with engine.begin() as conn:
        conn.execute(text(_EXTRACTED_INTELLIGENCE_DDL))
    log.info("migrations_extracted_intelligence_created")

    for stmt in _EXTRACTED_INTELLIGENCE_ALTER_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_extracted_intelligence_alter_skipped",
                statement=stmt,
                error=str(exc),
            )

    # 3. Create llm_audit_log table
    with engine.begin() as conn:
        conn.execute(text(_LLM_AUDIT_LOG_DDL))
    log.info("migrations_llm_audit_log_created")

    # 3b. Add token columns if table was created before they existed in DDL
    for stmt in _LLM_AUDIT_LOG_ALTER_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_llm_audit_log_alter_skipped",
                statement=stmt,
                error=str(exc),
            )

    # 4. employer_profiles is created via Base.metadata.create_all (UUID PK, FK to companies)

    # 4b. Create naics reference table (PostgreSQL DDL; other dialects rely on create_all)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(_NAICS_DDL))
        log.info("migrations_naics_created")

    # 4b. Analytics tables (posting_freshness, trajectory_map) — explicit DDL mirrors ORM models.
    with engine.begin() as conn:
        conn.execute(text(_POSTING_FRESHNESS_DDL))
        conn.execute(text(_TRAJECTORY_MAP_DDL))
        conn.execute(text(_DISRUPTION_FINGERPRINTS_DDL))
    log.info("migrations_analytics_tables_created")

    # 4c. Week 7 analytics aggregate tables (PostgreSQL DDL; ORM also registers via create_all)
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text(_WEEK7_ANALYTICS_AGGREGATES_DDL))
            log.info("migrations_week7_analytics_aggregates_created")
        except Exception as exc:
            log.warning(
                "migration_week7_analytics_aggregates_skipped",
                error=str(exc),
            )

    # 4e. Week 8 — cohort gap cache stub (Pair A owns disruption_fingerprints DDL)
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
CREATE TABLE IF NOT EXISTS dbo.cohort_gap_cache (
    id SERIAL PRIMARY KEY,
    cohort_key TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    gap_data JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""
                    )
                )
            log.info("migrations_cohort_gap_cache_stub_created")
        except Exception as exc:
            log.warning(
                "migration_cohort_gap_cache_stub_skipped",
                error=str(exc),
            )

    # 4f. Orchestration audit log + cohort_gap_cache unique cache key (Week 8 analytics API)
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                # Create table with columns matching OrchestrationAuditLog ORM model.
                conn.execute(
                    text(
                        """
CREATE TABLE IF NOT EXISTS dbo.orchestration_audit_log (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    correlation_id VARCHAR(128),
    endpoint VARCHAR(128) NOT NULL,
    question_hash VARCHAR(64),
    sql_hash VARCHAR(64),
    confidence DOUBLE PRECISION,
    success BOOLEAN NOT NULL,
    error_code VARCHAR(64),
    payload JSONB
);
"""
                    )
                )
                conn.execute(
                    text(
                        """
CREATE INDEX IF NOT EXISTS ix_orchestration_audit_log_created_at
    ON dbo.orchestration_audit_log (created_at);
"""
                    )
                )
                conn.execute(
                    text(
                        """
CREATE INDEX IF NOT EXISTS ix_orchestration_audit_correlation
    ON dbo.orchestration_audit_log (correlation_id);
"""
                    )
                )
            log.info("migrations_orchestration_audit_created")
        except Exception as exc:
            log.warning(
                "migration_orchestration_audit_skipped",
                error=str(exc),
            )
        # Idempotent column fixes for DBs created before the DDL was aligned with the ORM.
        # Safe to run on a fresh DB (IF NOT EXISTS / IF EXISTS guards each statement).
        try:
            with engine.begin() as conn:
                # Rename event_type → endpoint if the old column still exists.
                conn.execute(
                    text(
                        """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'dbo'
          AND table_name = 'orchestration_audit_log'
          AND column_name = 'event_type'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'dbo'
          AND table_name = 'orchestration_audit_log'
          AND column_name = 'endpoint'
    ) THEN
        ALTER TABLE dbo.orchestration_audit_log
            RENAME COLUMN event_type TO endpoint;
    END IF;
END $$;
"""
                    )
                )
                # Add missing columns that exist in the ORM but not in the original DDL.
                for col_ddl in [
                    "ALTER TABLE dbo.orchestration_audit_log ADD COLUMN IF NOT EXISTS question_hash VARCHAR(64)",
                    "ALTER TABLE dbo.orchestration_audit_log ADD COLUMN IF NOT EXISTS sql_hash VARCHAR(64)",
                    "ALTER TABLE dbo.orchestration_audit_log ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION",
                    "ALTER TABLE dbo.orchestration_audit_log ADD COLUMN IF NOT EXISTS error_code VARCHAR(64)",
                    # Drop stale columns from the original DDL that are absent from the ORM.
                    "ALTER TABLE dbo.orchestration_audit_log DROP COLUMN IF EXISTS source_agent",
                    "ALTER TABLE dbo.orchestration_audit_log DROP COLUMN IF EXISTS error_message",
                ]:
                    conn.execute(text(col_ddl))
                # Ensure the correlation_id index uses the canonical name from the ORM.
                conn.execute(
                    text(
                        """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'dbo'
          AND tablename = 'orchestration_audit_log'
          AND indexname = 'ix_orchestration_audit_correlation'
    ) THEN
        CREATE INDEX ix_orchestration_audit_correlation
            ON dbo.orchestration_audit_log (correlation_id);
    END IF;
END $$;
"""
                    )
                )
            log.info("migrations_orchestration_audit_columns_aligned")
        except Exception as exc:
            log.warning(
                "migration_orchestration_audit_column_fix_skipped",
                error=str(exc),
            )
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
CREATE UNIQUE INDEX IF NOT EXISTS uq_cohort_gap_cache_cohort_key
    ON dbo.cohort_gap_cache (cohort_key);
"""
                    )
                )
            log.info("migrations_cohort_gap_cache_unique_key_created")
        except Exception as exc:
            log.warning(
                "migration_cohort_gap_cache_unique_index_skipped",
                error=str(exc),
            )

    # 4d. sector_summary_weekly — align with Analytics Step 6 ORM (idempotent alters)
    if engine.dialect.name == "postgresql":
        for stmt in _SECTOR_SUMMARY_WEEKLY_ALTER_STATEMENTS:
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
            except Exception as exc:
                log.warning(
                    "migration_sector_summary_weekly_alter_skipped",
                    statement=stmt,
                    error=str(exc),
                )

    # 5. Add enrichment columns to dbo.job_postings (and related).
    #    Each ALTER runs in its own transaction so a single failure
    #    (e.g. job_postings not yet created) doesn't abort the rest.
    for stmt in _JOB_POSTINGS_ALTER_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_alter_skipped",
                statement=stmt,
                error=str(exc),
            )

    _ensure_duplicate_cluster_id_uuid(engine)
    _sync_agent_serial_sequences(engine)

    # 5b. FK job_postings.canonical_role_id → canonical_roles.role_id (PostgreSQL)
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text(_CANONICAL_ROLE_JOB_POSTING_FK))
            log.info("migrations_canonical_role_fk_added")
        except Exception as exc:
            log.warning(
                "migration_canonical_role_fk_skipped",
                error=str(exc),
            )

    for stmt in _NORMALIZED_JOBS_ALTER_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_normalized_jobs_alter_skipped",
                statement=stmt,
                error=str(exc),
            )

    for stmt in _COMPANIES_LOCATION_ALTER_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_companies_location_alter_skipped",
                statement=stmt,
                error=str(exc),
            )

    # 6. Drop legacy Prisma FK constraints and make NOT NULL columns nullable (#159).
    #    Pipeline stores location directly on job_postings, not via company_addresses.
    for stmt in _JOB_POSTINGS_LEGACY_CLEANUP:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_legacy_cleanup_skipped",
                statement=stmt,
                error=str(exc),
            )

    # Week 7 (after development base): skill_demand_weekly employer_count backfill
    for stmt in _SKILL_DEMAND_WEEKLY_ALTER_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_skill_demand_weekly_alter_skipped",
                statement=stmt,
                error=str(exc),
            )

    # Week 10 (#229): label_embedding vector column on canonical_roles (pgvector role router)
    for stmt in _CANONICAL_ROLES_ALTER_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_canonical_roles_alter_skipped",
                statement=stmt,
                error=str(exc),
            )

    # Issue #157: JSearch Pro-plan monthly API-request counter on job_ingestion_runs
    for stmt in _JOB_INGESTION_RUNS_ALTER_STATEMENTS:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:
            log.warning(
                "migration_job_ingestion_runs_alter_skipped",
                statement=stmt,
                error=str(exc),
            )

    # Week 8 (#176) — Q&A retrieval indexes on job_postings and extracted_intelligence.
    # Must run AFTER #170 ADD COLUMN statements so date_posted/seniority_level exist.
    if engine.dialect.name == "postgresql":
        for stmt in _QNA_RETRIEVAL_INDEX_STATEMENTS:
            try:
                with engine.begin() as conn:
                    conn.execute(text(stmt))
            except Exception as exc:
                log.warning(
                    "migration_qna_retrieval_index_skipped",
                    statement=stmt,
                    error=str(exc),
                )
        log.info("migrations_qna_retrieval_indexes_applied")

    # Week 10 (JIE #359) — concurrent btree indexes (safe on live DB; own try/except per statement).
    _apply_curriculum_concurrent_indexes(engine)

    # Labor Pulse — dbo.qa_feedback (Next.js POST upsert; unique on session_id + message_id)
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
CREATE TABLE IF NOT EXISTS dbo.qa_feedback (
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    feedback TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, message_id)
);
"""
                    )
                )
            log.info("migrations_qa_feedback_created")
        except Exception as exc:
            log.warning(
                "migration_qa_feedback_skipped",
                error=str(exc),
            )

    # Labor Pulse — dbo.conversation_log (Next.js POST; idempotent on session_id + message_id)
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
CREATE TABLE IF NOT EXISTS dbo.conversation_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    confidence DOUBLE PRECISION NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_log_session_message_unique UNIQUE (session_id, message_id)
);
"""
                    )
                )
            log.info("migrations_conversation_log_created")
        except Exception as exc:
            log.warning(
                "migration_conversation_log_skipped",
                error=str(exc),
            )

    log.info("migrations_complete")
