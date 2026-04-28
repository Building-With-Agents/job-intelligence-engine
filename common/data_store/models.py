"""SQLAlchemy ORM models for all database tables.

SQLAlchemy is the single database authority. All tables live in the ``dbo``
schema. Reference tables (companies, industry_sectors, etc.) are seeded via
``seed_pg_database.py`` and are agent-owned with full read+write.

Agent-created tables: raw_ingested_jobs, job_ingestion_runs, normalized_jobs,
    normalization_quarantine, extracted_intelligence, llm_audit_log,
    employer_profiles, canonical_roles, role_snapshot_weekly, disruption_fingerprints,
    analytics_pipeline_state, sector_summary_weekly, geo_demand_weekly,
    skill_demand_weekly, tool_demand_weekly, skill_velocity, skill_co_occurrence,
    cohort_gap_cache, orchestration_audit_log, qa_feedback, conversation_log,
    laborpulse_analytics_conversation, laborpulse_analytics_turn.
Reference tables (seeded, agent-owned): companies, industry_sectors,
    technology_areas, skills, socc, naics, job_postings.

job_postings agent-added columns (via run_migrations ALTER TABLE; not in Prisma schema):
  Phase 1:  source, external_id, ingestion_run_id, ai_relevance_score, quality_score,
            is_spam, spam_score, spam_tier, overall_confidence, field_confidence
  Phase 1b: soc_code, naics_code, temporal_period, borderplex_subregion,
            is_duplicate, duplicate_cluster_id, dedup_text_hash, dedup_embedding,
            zip_code, employer_profile_id, canonical_role_id
  Week 8 (#170, Q&A-ready):  date_posted, seniority_level, is_remote
  Week 8 (#173): role_classification (computed by classify_job() during enrichment)
  Week 8 (#174, structured salary): salary_min, salary_max, salary_currency, salary_period
                                    (legacy salary_range TEXT remains for backward compat)
  Deprecated (never write): employer_id, tech_area_id, location_id
    → FK constraints fk_job_postings_employers1, fk_job_postings_technology_areas1,
      fk_job_postings_company_addresses1 and their backing indexes are dropped by
      run_migrations. Columns remain NULL; do not reference in new SQL.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Literal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all agent models."""

    pass


from analytics._config import fresh_threshold_days as _fresh_threshold_days
from analytics._config import stale_threshold_days as _stale_threshold_days

FRESH_THRESHOLD_DAYS = _fresh_threshold_days()
STALE_THRESHOLD_DAYS = _stale_threshold_days()


def classify_freshness(days: int) -> Literal["fresh", "stale", "expired"]:
    """Classify posting age in whole days using FRESH_THRESHOLD_DAYS and STALE_THRESHOLD_DAYS."""
    if days <= FRESH_THRESHOLD_DAYS:
        return "fresh"
    if days <= STALE_THRESHOLD_DAYS:
        return "stale"
    return "expired"


# ---------------------------------------------------------------------------
# Ingestion tables
# ---------------------------------------------------------------------------


class RawIngestedJob(Base):
    """Staging table for raw job postings before normalization."""

    __tablename__ = "raw_ingested_jobs"
    __table_args__ = (
        UniqueConstraint("raw_payload_hash", name="uq_raw_ingested_jobs_hash"),
        Index("ix_raw_ingested_jobs_run_id", "ingestion_run_id"),
        Index("ix_raw_ingested_jobs_source_eid", "source", "external_id"),
        Index("ix_raw_ingested_jobs_status", "processing_status"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingestion_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    region_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Core fields
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Structured location (replaces single ``location`` column)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # URLs
    job_url: Mapped[str | None] = mapped_column(String(2083), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2083), nullable=True)

    # Date & classification
    date_posted: Mapped[str | None] = mapped_column(String(100), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Salary (raw extraction)
    salary_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    salary_period: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Raw payload
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Processing state
    processing_status: Mapped[str] = mapped_column(String(50), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_ingested: Mapped[datetime] = mapped_column(
        "ingestion_timestamp",
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class JobIngestionRun(Base):
    """Tracks each ingestion batch run for auditing and observability."""

    __tablename__ = "job_ingestion_runs"
    __table_args__ = (
        Index("ix_job_ingestion_runs_status", "status"),
        {"schema": "dbo"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    region_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="running")
    total_fetched: Mapped[int] = mapped_column(Integer, default=0)
    staged_count: Mapped[int] = mapped_column(Integer, default=0)
    dedup_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    # Upstream API requests consumed by this run (JSearch Pro budget tracker, issue #157).
    api_requests_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Normalization tables
# ---------------------------------------------------------------------------


class NormalizedJob(Base):
    """Post-normalization canonical job records."""

    __tablename__ = "normalized_jobs"
    __table_args__ = (
        Index("ix_normalized_jobs_run_id", "ingestion_run_id"),
        Index("ix_normalized_jobs_source_eid", "source", "external_id"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingestion_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    region_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Core fields
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Structured location (replaces location / normalized_location)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state_province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    work_arrangement: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # URL
    job_url: Mapped[str | None] = mapped_column(String(2083), nullable=True)

    # Classification
    employment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    occupation_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    naics_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    employer_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mapper_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Date
    date_posted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Salary
    salary_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    salary_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    salary_period: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Quality
    normalization_status: Mapped[str] = mapped_column(String(50), default="success")
    normalization_errors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Promotion audit (JIE #289): stamped by apply_enrichment_to_job_postings
    # on successful promotion to dbo.job_postings. NULL means "not yet promoted"
    # — picked up by scripts/sweep_unpromoted_normalized_jobs.py for retry
    # after a 1-hour grace window. Pre-#289 rows are NULL until the sweeper
    # or the one-shot backfill backfills them.
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NormalizationQuarantine(Base):
    """Records that failed normalization validation."""

    __tablename__ = "normalization_quarantine"
    __table_args__ = (
        Index("ix_norm_quarantine_run_id", "ingestion_run_id"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingestion_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_type: Mapped[str] = mapped_column(String(100), nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    quarantined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Extraction tables (Week 4)
# ---------------------------------------------------------------------------


class ExtractedIntelligence(Base):
    """Extraction results for the 6-dimension model (skills, tools, tasks,
    responsibilities, context) plus cost and quality metadata.

    Source of truth: ARCHITECTURE_DEEP.md § extracted_intelligence table.
    """

    __tablename__ = "extracted_intelligence"
    __table_args__ = (
        Index("ix_extracted_intelligence_norm_id", "normalized_job_id"),
        Index("ix_extracted_intelligence_failed", "extraction_failed"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    normalized_job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("dbo.normalized_jobs.id"),
        nullable=False,
    )
    extraction_version: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    extraction_model: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extraction_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # 6-dimension JSONB columns
    skills: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    tools: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    tasks: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    responsibilities: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    context: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)

    # Quality metadata
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    extraction_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Full ExtractionMetadata blob for audit/debugging (from guardrails branch)
    extraction_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


# ---------------------------------------------------------------------------
# LLM Audit Log
# ---------------------------------------------------------------------------


class LLMAuditLog(Base):
    """Centralized audit log for every LLM call across all agents.

    Columns: id (PK; checklist: log_id), agent_name, prompt_hash, model, provider,
    latency_ms, input_tokens, output_tokens, token_count, cost_usd, success,
    error_reason, created_at.
    """

    __tablename__ = "llm_audit_log"
    __table_args__ = (
        Index("ix_llm_audit_log_agent_name", "agent_name"),
        Index("ix_llm_audit_log_created_at", "created_at"),
        Index("ix_llm_audit_log_success", "success"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Enrichment tables (Week 5)
# ---------------------------------------------------------------------------


class EmployerProfile(Base):
    """Company-level enrichment: size, AI maturity, sector, known-employer flag.

    One row per ``companies.company_id``; job postings reference via ``employer_profile_id``.
    """

    __tablename__ = "employer_profiles"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_employer_profiles_company_id"),
        Index("ix_employer_profiles_company_id", "company_id"),
        {"schema": "dbo"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("dbo.companies.company_id", ondelete="CASCADE"),
        nullable=False,
    )
    company_size: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    ai_maturity_signal: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    sector: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_known_employer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Analytics — Week 7 canonical role clustering (Pair C, IMP-023)
# ---------------------------------------------------------------------------


class CanonicalRole(Base):
    """Unsupervised cluster -> human-readable canonical role (HDBSCAN + embeddings).

    ``role_id`` is the stable external key (FK from ``job_postings.canonical_role_id``
    and ``role_snapshot_weekly.canonical_role_id``). ``cluster_centroid`` stores the
    mean embedding as a JSON array of floats (same dimension as posting embeddings).

    See: ``docs/week 7/WEEK-07-canonical-role-clustering-bryan-emilio-runbook.md``
    and ``.cursor/rules/canonical-role-clustering.mdc``.
    """

    __tablename__ = "canonical_roles"
    __table_args__ = (
        Index("ix_canonical_roles_computed_at", "computed_at"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    posting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cluster_centroid: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    representative_titles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    top_skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    top_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_llm_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class RoleSnapshotWeekly(Base):
    """Weekly aggregates per canonical role (step 5 of the analytics pipeline).

    Salary percentiles align with Pair B helper / runbook; ``avg_salary`` and
    ``median_salary`` support dashboards and ARCHITECTURE_DEEP examples.
    """

    __tablename__ = "role_snapshot_weekly"
    __table_args__ = (
        UniqueConstraint("week_start", "canonical_role_id", name="uq_role_snapshot_weekly_week_role"),
        Index("ix_role_snapshot_weekly_week_start", "week_start"),
        Index("ix_role_snapshot_weekly_canonical_role_id", "canonical_role_id"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    canonical_role_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("dbo.canonical_roles.role_id", ondelete="RESTRICT"),
        nullable=False,
    )
    posting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    avg_salary: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_salary: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    salary_p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    top_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Analytics (Week 7) — posting freshness (Pair D)
# ---------------------------------------------------------------------------


class PostingFreshness(Base):
    """Per-job-posting freshness snapshot for analytics (dbo.posting_freshness). Runbook schema."""

    __tablename__ = "posting_freshness"
    __table_args__ = (
        Index("ix_posting_freshness_posting_id", "posting_id"),
        {"schema": "dbo"},
    )

    posting_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_repost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    repost_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fill_proxy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class TrajectoryMap(Base):
    """Phase 2 scaffold -- trajectory data per role (dbo.trajectory_map). Empty data, schema only."""

    __tablename__ = "trajectory_map"
    __table_args__ = ({"schema": "dbo"},)

    role_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trajectory_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class DisruptionFingerprint(Base):
    """Persisted disruption fingerprint per canonical role (Analytics step 12, issue #108).

    One row per ``canonical_role_id``; ``session.merge`` replaces metrics on refresh.
    """

    __tablename__ = "disruption_fingerprints"
    __table_args__ = (
        Index("ix_disruption_fingerprints_computed_at", "computed_at"),
        {"schema": "dbo"},
    )

    canonical_role_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    disruption_category: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    disruption_intensity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    skill_velocity: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tool_transition: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    task_shift: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    responsibility_expansion: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ai_intensity_trend: Mapped[str] = mapped_column(Text, nullable=False, default="stable")
    workflow_restructuring_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trajectory: Mapped[str] = mapped_column(Text, nullable=False, default="stable")
    period_comparison: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    content_fingerprint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Analytics aggregate tables (Week 7 -- Pair A)
# Frozen column contract: .cursor/rules/skill-tool-demand.mdc (IMP-021).
# ---------------------------------------------------------------------------


class SkillDemandWeekly(Base):
    """Weekly skill demand counts (Analytics step 2).

    ``employer_count`` stores distinct employers for the skill in the week
    (``func.count(func.distinct(company_id))`` pattern in SQL -- IMP-021).
    """

    __tablename__ = "skill_demand_weekly"
    __table_args__ = (
        UniqueConstraint("skill_label", "week_start", name="uq_skill_demand_week"),
        Index("ix_skill_demand_weekly_week", "week_start"),
        Index("ix_skill_demand_weekly_skill", "skill_label"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_label: Mapped[str] = mapped_column(Text, nullable=False)
    esco_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    posting_count: Mapped[int] = mapped_column(Integer, nullable=False)
    employer_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolDemandWeekly(Base):
    """Weekly tool demand counts (Analytics step 3)."""

    __tablename__ = "tool_demand_weekly"
    __table_args__ = (
        UniqueConstraint("tool_label", "week_start", name="uq_tool_demand_week"),
        Index("ix_tool_demand_weekly_week", "week_start"),
        Index("ix_tool_demand_weekly_tool", "tool_label"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_label: Mapped[str] = mapped_column(Text, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    posting_count: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillVelocity(Base):
    """Skill demand velocity / trend (Analytics step 8).

    Python attribute ``velocity_week`` maps to DB column ``week`` (reserved name).
    """

    __tablename__ = "skill_velocity"
    __table_args__ = (
        UniqueConstraint("skill_label", "week", name="uq_skill_velocity_week"),
        Index("ix_skill_velocity_week", "week"),
        Index("ix_skill_velocity_skill", "skill_label"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_label: Mapped[str] = mapped_column(Text, nullable=False)
    esco_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    velocity_week: Mapped[date] = mapped_column("week", Date, nullable=False)
    demand_count: Mapped[int] = mapped_column(Integer, nullable=False)
    week_over_week_change: Mapped[float] = mapped_column(Float, nullable=False)
    four_week_trend: Mapped[str] = mapped_column(Text, nullable=False)
    trend_confidence: Mapped[float] = mapped_column(Float, nullable=False)


class SkillCoOccurrence(Base):
    """Skill pair co-occurrence within a week (Analytics step 9)."""

    __tablename__ = "skill_co_occurrence"
    __table_args__ = (
        UniqueConstraint("skill_a", "skill_b", "week_start", name="uq_skill_co_occurrence_week"),
        Index("ix_skill_co_occurrence_week", "week_start"),
        Index("ix_skill_co_occurrence_skills", "skill_a", "skill_b"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_a: Mapped[str] = mapped_column(Text, nullable=False)
    skill_b: Mapped[str] = mapped_column(Text, nullable=False)
    co_occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ===========================================================================
# Reference tables — seeded via seed_pg_database.py, agent-owned (full read+write).
#
# create_all() with checkfirst=True will skip creation if tables exist.
# ===========================================================================


class Company(Base):
    """Companies table — originally Prisma-managed, now agent-owned.

    Agents can read existing companies and write placeholders during
    enrichment (company resolution).
    """

    __tablename__ = "companies"
    __table_args__ = {"schema": "dbo"}

    company_id: Mapped[str] = mapped_column(Text, primary_key=True)
    industry_sector_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    # HQ / geo (schema consolidation #110; added via run_migrations if missing)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    about_us: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    year_founded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    company_website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_mission: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_vision: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[str | None] = mapped_column(Text, default="1-10")
    estimated_annual_hires: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    createdby: Mapped[str | None] = mapped_column(Text, nullable=True)
    createdat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updatedat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    engagementtype: Mapped[str | None] = mapped_column(String(1000), default="Lead")


class IndustrySector(Base):
    """Industry sectors reference table — agent-owned."""

    __tablename__ = "industry_sectors"
    __table_args__ = {"schema": "dbo"}

    industry_sector_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sector_title: Mapped[str] = mapped_column(Text, nullable=False)
    createdat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updatedat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TechnologyArea(Base):
    """Technology areas reference table — agent-owned."""

    __tablename__ = "technology_areas"
    __table_args__ = {"schema": "dbo"}

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    createdat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updatedat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Skill(Base):
    """Skills reference table — agent-owned.

    Note: The ``embedding`` column (pgvector vector(1536)) is not mapped here
    because SQLAlchemy needs the pgvector extension. Access it via raw SQL if
    needed for similarity search.
    """

    __tablename__ = "skills"
    __table_args__ = {"schema": "dbo"}

    skill_id: Mapped[str] = mapped_column(Text, primary_key=True)
    skill_subcategory_id: Mapped[str] = mapped_column(Text, nullable=False)
    skill_name: Mapped[str] = mapped_column(Text, nullable=False)
    skill_info_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    skill_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    createdat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updatedat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SOCC(Base):
    """Standard Occupational Classification Codes — agent-owned.

    Used by enrichment for SOC code classification.
    """

    __tablename__ = "socc"
    __table_args__ = {"schema": "dbo"}

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(String(1000), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    createdat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updatedat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PostalGeoData(Base):
    """US zip code reference — read-only lookup table.

    Provides city, county, state, lat/lng for zip code resolution.
    Used by normalization to resolve zip_code from city+state when
    the source posting doesn't include a zip. County and other fields
    are always looked up via JOIN, never stored redundantly on job tables.
    """

    __tablename__ = "postal_geo_data"
    __table_args__ = {"schema": "dbo"}

    zip: Mapped[str] = mapped_column(String(5), primary_key=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    county: Mapped[str] = mapped_column(String(100), nullable=False)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)


class NAICS(Base):
    """NAICS 2022 US taxonomy — agent-owned.

    Primary key is the official NAICS code (2–6 digit hierarchical code).
    Seeded from ``data/naics-2022-taxonomy-reference.xlsx``.
    """

    __tablename__ = "naics"
    __table_args__ = {"schema": "dbo"}

    naics_code: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    seq_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    createdat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updatedat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Analytics pipeline state (Week 7 — minimum-data guard watermark)
# ---------------------------------------------------------------------------


class AnalyticsPipelineState(Base):
    """Singleton row ``id = 1``: last time analytics completed and emitted ``AnalyticsRefreshed``.

    The analytics agent reads ``last_successful_run_at`` to count new ``job_postings``
    rows since the previous successful run. Updated only after the guard passes and
    the pipeline finishes (same session as downstream aggregate writes in Week 7).
    """

    __tablename__ = "analytics_pipeline_state"
    __table_args__ = {"schema": "dbo"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_successful_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Analytics aggregates (Week 7 — Pair B)
# ---------------------------------------------------------------------------


class SectorSummaryWeekly(Base):
    """Weekly aggregates by industry sector (Pair B — Analytics Step 6).

    ``avg_salary`` stores the salary **median (p50)** (same basis as
    :func:`analytics.aggregators.salary_percentiles.compute_salary_percentiles`).
    ``top_skills`` is the top 10 most frequent extracted ``skill_name`` values for the sector-week.
    """

    __tablename__ = "sector_summary_weekly"
    __table_args__ = {"schema": "dbo"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    sector: Mapped[str] = mapped_column(Text, nullable=False)
    posting_count: Mapped[int] = mapped_column(Integer, nullable=False)
    employer_count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_salary: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class GeoDemandWeekly(Base):
    """Weekly job counts by enrichment ``borderplex_subregion``."""

    __tablename__ = "geo_demand_weekly"
    __table_args__ = {"schema": "dbo"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    borderplex_subregion: Mapped[str] = mapped_column(String(32), nullable=False)
    posting_count: Mapped[int] = mapped_column(Integer, nullable=False)


# ---------------------------------------------------------------------------
# Week 8 — Analytics API cache + orchestration audit
# ---------------------------------------------------------------------------


class CohortGapCache(Base):
    """Cached payloads for on-demand analytics triggers (read-through cache)."""

    __tablename__ = "cohort_gap_cache"
    __table_args__ = (
        UniqueConstraint("trigger_type", "request_hash", name="uq_cohort_gap_cache_trig_hash"),
        Index("ix_cohort_gap_cache_computed_at", "computed_at"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cohort_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    gap_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrchestrationAuditLog(Base):
    """Orchestration / Q&A audit trail (append-only)."""

    __tablename__ = "orchestration_audit_log"
    __table_args__ = (
        Index("ix_orchestration_audit_log_created_at", "created_at"),
        Index("ix_orchestration_audit_correlation", "correlation_id"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    question_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sql_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


# ---------------------------------------------------------------------------
# Labor Pulse — Q&A feedback (Next.js API upsert)
# ---------------------------------------------------------------------------


class QaFeedback(Base):
    """Thumbs / feedback on a Q&A turn; upsert key is (session_id, message_id)."""

    __tablename__ = "qa_feedback"
    __table_args__ = {"schema": "dbo"}

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Labor Pulse — conversation log (Next.js API; idempotent on session + message)
# ---------------------------------------------------------------------------


class ConversationLog(Base):
    """Completed Q&A turn for session analytics; unique (session_id, message_id)."""

    __tablename__ = "conversation_log"
    __table_args__ = (
        UniqueConstraint("session_id", "message_id", name="conversation_log_session_message_unique"),
        {"schema": "dbo"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# LaborPulse — JIE #223 multi-turn Q&A memory (Postgres; survives restarts)
# ---------------------------------------------------------------------------


class LaborPulseAnalyticsConversation(Base):
    """One LaborPulse thread: ``conversation_id`` UUID + scoping to tenant and user email."""

    __tablename__ = "laborpulse_analytics_conversation"
    __table_args__ = (
        Index("ix_laborpulse_ac_tenant_user", "tenant_id", "user_email"),
        {"schema": "dbo"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class LaborPulseAnalyticsTurn(Base):
    """Q/A turn row for follow-up context (JIE #223)."""

    __tablename__ = "laborpulse_analytics_turn"
    __table_args__ = (
        Index("ix_laborpulse_at_conv", "conversation_id", "turn_index"),
        UniqueConstraint("conversation_id", "turn_index", name="uq_laborpulse_turn_conv_idx"),
        {"schema": "dbo"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("dbo.laborpulse_analytics_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    intent_label: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
