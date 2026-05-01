"""Pydantic contracts for Ask the Data: router output → evidence → synthesis.

The **intent + SQL routing** workstream fills **`QueryResultPayload`** after guardrailed execution.
The **synthesis workstream** owns `EvidenceBundle`, `SynthesisResponse`, and cost ledger types.

Import from `analytics.query_engine` or this module. See `.cursor/rules/analytics-qna-synthesis.mdc`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from common.types.query_request import QueryRequest


class DataSufficiency(StrEnum):
    """Coarse data-availability state for policy + UX copy."""

    NO_DATA = "no_data"
    SPARSE = "sparse"
    ADEQUATE = "adequate"


class LLMCallCost(BaseModel):
    """One LLM invocation in the Q&A path (classification, routing, synthesis, follow-up)."""

    leg: str = Field(
        ...,
        description="Stable key, e.g. intent_classification | sql_generation | synthesis | follow_up",
    )
    cost_usd: float = Field(ge=0.0, default=0.0)
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    model: str | None = Field(default=None, description="Deployment or model id as reported by adapter.")


class CostLedger(BaseModel):
    """Accumulate per-query spend; sum equals total for `SynthesisResponse`."""

    legs: list[LLMCallCost] = Field(default_factory=list)

    def total_usd(self) -> float:
        return round(sum(x.cost_usd for x in self.legs), 6)

    def add_leg(self, leg: LLMCallCost) -> None:
        self.legs.append(leg)


class QueryResultPayload(BaseModel):
    """Handoff from intent + SQL routing to evidence and synthesis.

    Populate `request` with the original user question; fill SQL/row fields after guardrailed execution.
    """

    request: QueryRequest
    intent_label: str = Field(
        ..., min_length=1, description="Classifier output, e.g. trend_compare | aggregate_skill_demand."
    )
    classification_confidence: float = Field(..., ge=0.0, le=1.0)

    executed_sql: str | None = Field(
        default=None,
        description="Approved SELECT only; omit or redact in logs if policy requires.",
    )
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count_returned: int = Field(ge=0, default=0)
    result_truncated: bool = Field(
        default=False,
        description="True if LIMIT or guardrails capped rows and more rows may exist.",
    )
    tables_referenced: list[str] = Field(
        default_factory=list,
        description="Table names from allowlist that appeared in the executed query.",
    )
    execution_time_ms: float | None = Field(default=None, ge=0.0)

    router_error: str | None = Field(
        default=None,
        description="Set when SQL was not executed or failed validation; synthesis should refuse or explain.",
    )
    sql_execution_error_detail: str | None = Field(
        default=None,
        description=(
            "When execution fails after guardrails, truncated DB/driver message (single line, "
            "no user query text) for operators and UI; never log raw user input here."
        ),
    )
    correlation_id: str | None = Field(
        default=None,
        description="Optional pipeline id for tracing; do not log PII.",
    )
    distinct_posting_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "When the router computes COUNT(DISTINCT job_posting_id) (or equivalent), set this "
            "for volume policy; overrides row-sum heuristics in build_evidence_bundle."
        ),
    )
    role_suggestion_hint: str | None = Field(
        default=None,
        description=(
            "JIE #298 — set by the router when a role-filtered curriculum / workflow / "
            "role_evolution query returns 0 rows. Replaces the generic 'No data in scope' "
            "refusal with a live-fetched suggestion from dbo.canonical_roles."
        ),
    )
    no_data_refusal_override: str | None = Field(
        default=None,
        description=(
            "JIE #330 — deterministic refusal when the router returns empty rows before aggregate "
            "SQL (skill taxonomy miss or geo/skill scope). Used by evidence instead of the "
            "generic 'No data in scope' line when set."
        ),
    )


class EvidenceCitation(BaseModel):
    """A single citeable unit; synthesis must not invent numbers beyond these fields."""

    citation_id: str = Field(..., min_length=1, description="Stable id for UI / tracing within one response.")
    summary: str = Field(
        ...,
        min_length=1,
        description="Short factual line the model may paraphrase, e.g. median salary USD 72k from 84 rows.",
    )
    source_table: str | None = None
    supporting_count: int | None = Field(
        default=None,
        ge=0,
        description="Postings or rows supporting this citation when applicable.",
    )
    time_period: str | None = Field(
        default=None,
        description="ISO range or human period covered by this citation.",
    )


class EvidenceBundle(BaseModel):
    """Built *before* the main synthesis LLM call (truth layer)."""

    facts: list[EvidenceCitation] = Field(default_factory=list)
    period_coverage: str = Field(
        default="",
        description="Required user-facing description of time range(s), even if 'period unknown'.",
    )
    volume_posting_count: int | None = Field(
        default=None,
        ge=0,
        description="Canonical N for volume policy (e.g. distinct postings); None if not applicable.",
    )
    sufficiency: DataSufficiency = DataSufficiency.NO_DATA
    blended_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    confidence_explanation: str = Field(
        default="",
        description="Actionable explanation for transparency flags, not only 'low confidence'.",
    )
    refuse_synthesis: bool = Field(
        default=False,
        description="If True, skip confident natural-language claims; use refusal path.",
    )
    refusal_reason: str | None = Field(
        default=None,
        description="Shown or logged when refusing; no PII.",
    )
    sql_execution_error_detail: str | None = Field(
        default=None,
        description="Echo of payload.sql_execution_error_detail when SQL execution failed; for UI expanders.",
    )


class SynthesisResponse(BaseModel):
    """API-facing result after synthesis and follow-ups."""

    answer_text: str = Field(default="", description="Empty when fully refused.")
    citations: list[EvidenceCitation] = Field(default_factory=list)

    periods_described: str = Field(
        default="",
        description="Echo or restate period_coverage for API consumers / UI.",
    )
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    confidence_flagged_low: bool = Field(
        default=False,
        description="True when confidence < CONFIDENCE_TRANSPARENCY_THRESHOLD.",
    )
    confidence_explanation: str | None = None

    volume_flagged_low: bool = Field(
        default=False,
        description="True when volume_posting_count < VOLUME_WARNING_POSTING_THRESHOLD.",
    )
    volume_warning: str | None = None

    refused: bool = False
    refusal_message: str | None = None
    sql_execution_error_detail: str | None = Field(
        default=None,
        description="PostgreSQL/driver error snippet when execute failed after guardrails; None otherwise.",
    )

    follow_up_questions: list[str] = Field(
        default_factory=list,
        description="2–3 contextual questions; empty if skipped or errored.",
    )

    total_cost_usd: float = Field(ge=0.0, default=0.0)
    cost_breakdown_usd: dict[str, float] = Field(
        default_factory=dict,
        description="Optional map leg -> USD for debugging and dashboards.",
    )
