"""Pydantic contracts for POST /analytics/query and trigger endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """Stable citation shape for dashboard consumers."""

    title: str = ""
    source: str = ""
    snippet: str = ""


class AnalyticsQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    correlation_id: str | None = Field(default=None, max_length=128)


class AnalyticsQueryResponse(BaseModel):
    answer: str
    evidence: list[EvidenceItem]
    confidence: float
    follow_up_questions: list[str]
    sql_generated: str
    cost_usd: float


class TriggerEnvelope(BaseModel):
    trigger: str
    cached: bool
    computed_at: str
    data: dict[str, Any]


class CohortGapAnalysisRequest(BaseModel):
    cohort_key: str = Field(..., min_length=1, max_length=256)
    week_start: str | None = Field(default=None, description="ISO date YYYY-MM-DD")


class RoleBenchmarkRequest(BaseModel):
    canonical_role_id: str = Field(..., min_length=1, max_length=128)
    week_start: str | None = None


class EmergingSkillsScanRequest(BaseModel):
    week_start: str | None = None
    min_posting_count: int = Field(default=1, ge=1, le=10000)


class CustomEmployerComparisonRequest(BaseModel):
    company_id: str = Field(..., min_length=1, max_length=128)
    week_start: str | None = None
