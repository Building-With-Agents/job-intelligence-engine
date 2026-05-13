"""Pydantic contracts for POST /analytics/query and trigger endpoints."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class EvidenceItem(BaseModel):
    """Stable citation shape for dashboard consumers."""

    title: str = ""
    source: str = ""
    snippet: str = ""
    supporting_count: int | None = None
    time_period: str | None = None


class AnalyticsQueryRequest(BaseModel):
    """Legacy Week 8 request shape (dashboard / older clients)."""

    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(
        ...,
        min_length=1,
        max_length=20_000,
        validation_alias=AliasChoices("question", "query"),
    )
    correlation_id: str | None = Field(default=None, max_length=128)


class LaborPulseQueryRequest(BaseModel):
    """LaborPulse / wfd-os ``POST /analytics/query`` JSON body (JIE #222)."""

    model_config = ConfigDict(extra="ignore")

    question: str = Field(..., max_length=20_000)
    conversation_id: str | None = Field(
        default=None,
        max_length=36,
        description="Optional UUID for multi-turn; omitted on first turn.",
    )


class LaborPulseEvidenceItem(BaseModel):
    """Citation shape for LaborPulse / wfd-os ``QueryResponse`` (JIE #225)."""

    title: str = ""
    source: str = ""
    snippet: str = ""
    supporting_count: int | None = None
    time_period: str | None = None


class LaborPulseQueryResponse(BaseModel):
    """Wire JSON for ``POST /analytics/query`` — align with wfd-os ``QueryResponse`` (JIE #225)."""

    conversation_id: str
    answer: str
    evidence: list[LaborPulseEvidenceItem]
    confidence: Literal["low", "medium", "high"]
    follow_up_questions: list[str]
    cost_usd: float
    sql_generated: str


class AnalyticsQueryResponse(BaseModel):
    answer: str
    evidence: list[EvidenceItem]
    confidence: float
    classified_intent: str = "other"
    intent_classification_confidence: float = 0.0
    periods_described: str = ""
    confidence_flagged_low: bool = False
    confidence_explanation: str | None = None
    volume_flagged_low: bool = False
    volume_warning: str | None = None
    refused: bool = False
    refusal_message: str | None = None
    sql_execution_error_detail: str | None = None
    follow_up_questions: list[str]
    sql_generated: str
    cost_usd: float
    total_cost_usd: float = 0.0
    cost_breakdown_usd: dict[str, float] = Field(default_factory=dict)
    row_count_returned: int = Field(
        default=0,
        ge=0,
        description="Rows returned from guardrailed SQL for this question (0 if none or not executed).",
    )


class TriggerEnvelope(BaseModel):
    trigger: str
    cached: bool
    computed_at: str
    data: dict[str, Any]


class CohortGapTriggerCacheData(BaseModel):
    """JSON written to ``CohortGapCache.gap_data`` for cohort gap analysis."""

    model_config = ConfigDict(extra="forbid")

    cohort_key: str
    week_start: str | None = None
    market_skill_demand: list[dict[str, Any]] = Field(default_factory=list)


class RoleBenchmarkTriggerCacheData(BaseModel):
    """JSON written to ``CohortGapCache.gap_data`` for role benchmark triggers."""

    model_config = ConfigDict(extra="forbid")

    canonical_role_id: str
    week_start: str | None = None
    snapshots: list[dict[str, Any]] = Field(default_factory=list)


class EmergingSkillsTriggerCacheData(BaseModel):
    """JSON written to ``CohortGapCache.gap_data`` for emerging skills scan."""

    model_config = ConfigDict(extra="forbid")

    week_start: str | None = None
    skills: list[dict[str, Any]] = Field(default_factory=list)


class EmployerComparisonTriggerCacheData(BaseModel):
    """JSON written to ``CohortGapCache.gap_data`` for custom employer comparison."""

    model_config = ConfigDict(extra="forbid")

    company_id: str
    week_start: str | None = None
    market_sector_context: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


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
