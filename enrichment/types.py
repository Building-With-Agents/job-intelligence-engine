"""Typed shapes at enrichment agent boundaries (Pair C P3).

``RecordEnrichedPayload`` validates the flat dict passed into
:func:`enrichment.job_postings_promotion.apply_enrichment_to_job_postings`.
Unknown keys are **ignored** (``extra="ignore"``) so forward-compatible payloads
and merged in-memory dicts do not fail validation; malformed **types** fail with
``pydantic.ValidationError``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecordEnrichedPayload(BaseModel):
    """Subset of keys consumed by job postings promotion (single-record path)."""

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
