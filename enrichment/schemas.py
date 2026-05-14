"""Pydantic schemas for enrichment output (Week 6 external data integration).

``EnrichedJobProfile`` aggregates job context with optional BLS / O*NET / Census payloads.
``job_record`` is a ``dict`` for pipeline compatibility (same shape as a serialized
:class:`~common.types.job_record.JobRecord` plus extraction fields where present).

``RecordEnrichedPayload`` is the typed single-record shape for
:func:`enrichment.job_postings_promotion.apply_enrichment_to_job_postings` (Pair C P3).

Cross-pair field names (``soc_code``, ``naics_code``, canonical ``employer`` vs
``employer_profile``): ``.cursor/rules/integration-schema.mdc`` § Nestor + Fatima.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from enrichment.adapters.models import OccupationProfile, RegionalProfile, WageEstimate


class RecordEnrichedPayload(BaseModel):
    """Subset of keys consumed by job postings promotion (single-record path).

    Validates the flat dict passed into
    :func:`enrichment.job_postings_promotion.apply_enrichment_to_job_postings`.
    Unknown keys are **ignored** (``extra="ignore"``) so forward-compatible payloads
    and merged in-memory dicts do not fail validation; malformed **types** fail with
    ``pydantic.ValidationError``.
    """

    model_config = ConfigDict(extra="ignore")

    spam_tier: str | None = None
    spam_score: float | None = None
    quality_score: float | None = None
    quality_components: dict[str, Any] = Field(default_factory=dict)
    field_confidence: dict[str, Any] = Field(default_factory=dict)
    overall_confidence: float | None = None
    naics_code: str | None = None
    soc_code: str | None = None
    role_classification: str | None = None
    seniority: str | None = None
    seniority_level: str | None = None
    employer_metadata: dict[str, Any] | None = None
    # Forward-compatible only: merged in-memory dicts / event-shaped payloads may carry
    # ``company_id``; job postings promotion uses ``resolved["company_id"]`` from the DB
    # resolve path, not this field, for employer profile SQL and posting updates.
    company_id: str | None = None

    @field_validator("spam_score", "quality_score", "overall_confidence", mode="before")
    @classmethod
    def _coerce_optional_float(cls, v: Any) -> float | None:
        if v is None:
            return None
        if isinstance(v, bool):
            raise ValueError("boolean is not a valid numeric score")
        if isinstance(v, (int, float)):
            return float(v)
        raise ValueError(f"expected int or float, got {type(v).__name__}")

    @field_validator("quality_components", "field_confidence", mode="before")
    @classmethod
    def _coerce_confidence_maps(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return dict(v)
        raise ValueError("expected dict or null")

    @field_validator("employer_metadata", mode="before")
    @classmethod
    def _coerce_employer_metadata(cls, v: Any) -> dict[str, Any] | None:
        if v is None:
            return None
        if isinstance(v, dict):
            return dict(v)
        raise ValueError("expected dict or null")


class EnrichedJobProfile(BaseModel):
    """Enriched view of a job with optional external reference data.

    Nestor + Fatima lock ``soc_code`` / ``naics_code``; nested employer data lives in
    ``employer_profile`` (dict) until renamed to pair-canonical ``employer``.
    """

    job_record: dict[str, Any] = Field(
        ...,
        description="Canonical job row as dict (JobRecord-compatible plus enrichment fields).",
    )
    temporal_period: str | None = None
    borderplex_subregion: str | None = None
    employer_profile: dict[str, Any] | None = None
    soc_code: str | None = None
    naics_code: str | None = None
    is_duplicate: bool = False
    duplicate_cluster_id: str | None = None
    wage_estimate: WageEstimate | None = None
    occupation_profile: OccupationProfile | None = None
    regional_profile: RegionalProfile | None = None
