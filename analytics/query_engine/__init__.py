"""Ask the Data: routing populates `QueryResultPayload`; evidence + synthesis consume it."""

from __future__ import annotations

from analytics.query_engine.constants import (
    CONFIDENCE_TRANSPARENCY_THRESHOLD,
    VOLUME_WARNING_POSTING_THRESHOLD,
)
from analytics.query_engine.evidence import build_evidence_bundle
from analytics.query_engine.qna import run_analytics_qna
from analytics.query_engine.routing import run_guardrailed_analytics_query
from analytics.query_engine.schemas import (
    CostLedger,
    DataSufficiency,
    EvidenceBundle,
    EvidenceCitation,
    LLMCallCost,
    QueryResultPayload,
    SynthesisResponse,
)
from analytics.query_engine.sql_guardrails import ALLOWED_TABLES, validate_sql
from analytics.query_engine.synthesis import synthesize_answer

__all__ = [
    "CONFIDENCE_TRANSPARENCY_THRESHOLD",
    "VOLUME_WARNING_POSTING_THRESHOLD",
    "ALLOWED_TABLES",
    "CostLedger",
    "DataSufficiency",
    "EvidenceBundle",
    "EvidenceCitation",
    "LLMCallCost",
    "QueryResultPayload",
    "SynthesisResponse",
    "build_evidence_bundle",
    "run_analytics_qna",
    "run_guardrailed_analytics_query",
    "synthesize_answer",
    "validate_sql",
]
