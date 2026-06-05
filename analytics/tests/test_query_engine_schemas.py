"""Contract tests for analytics.query_engine Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from analytics.query_engine.constants import (
    CONFIDENCE_TRANSPARENCY_THRESHOLD,
    VOLUME_WARNING_POSTING_THRESHOLD,
)
from analytics.query_engine.fixtures import sample_query_result_payload_ok
from analytics.query_engine.schemas import (
    CostLedger,
    DataSufficiency,
    EvidenceBundle,
    EvidenceCitation,
    LLMCallCost,
    QueryResultPayload,
    SynthesisResponse,
)
from common.types.query_request import QueryRequest


def test_query_result_payload_roundtrip() -> None:
    p = sample_query_result_payload_ok()
    raw = p.model_dump(mode="json")
    p2 = QueryResultPayload.model_validate(raw)
    assert p2.intent_label == p.intent_label
    assert p2.rows == p.rows
    assert p2.request.query == p.request.query


def test_cost_ledger_total() -> None:
    ledger = CostLedger(
        legs=[
            LLMCallCost(leg="intent", cost_usd=0.001, input_tokens=100, output_tokens=20),
            LLMCallCost(leg="synthesis", cost_usd=0.02, input_tokens=500, output_tokens=200),
        ]
    )
    assert ledger.total_usd() == pytest.approx(0.021)


def test_synthesis_response_flags() -> None:
    resp = SynthesisResponse(
        answer_text="Based on 84 postings, median salary was USD 72k.",
        citations=[
            EvidenceCitation(
                citation_id="c1",
                summary="Median 72k, N=84",
                source_table="job_postings",
                supporting_count=84,
                time_period="2025-Q1",
            )
        ],
        periods_described="2025-Q1",
        confidence=0.55,
        confidence_flagged_low=True,
        confidence_explanation="Intent confidence 0.82 but narrow geographic filter.",
        volume_flagged_low=False,
        total_cost_usd=0.03,
        cost_breakdown_usd={"intent": 0.001, "synthesis": 0.029},
    )
    assert resp.confidence < CONFIDENCE_TRANSPARENCY_THRESHOLD
    assert resp.confidence_flagged_low is True


def test_volume_threshold_constant_documented() -> None:
    assert VOLUME_WARNING_POSTING_THRESHOLD == 30


def test_evidence_bundle_no_data() -> None:
    b = EvidenceBundle(
        sufficiency=DataSufficiency.NO_DATA,
        refuse_synthesis=True,
        refusal_reason="Query returned zero rows for the selected filters.",
    )
    assert b.sufficiency == DataSufficiency.NO_DATA


def test_query_result_payload_rejects_empty_intent_label() -> None:
    with pytest.raises(ValidationError):
        QueryResultPayload(
            request=QueryRequest(query="x"),
            intent_label="",
            classification_confidence=0.9,
        )


# ---------------------------------------------------------------------------
# LaborPulseQueryRequest schema validators (added in PR #424 hardening)
# ---------------------------------------------------------------------------


def test_laborpulse_query_request_strips_whitespace() -> None:
    from analytics.api.schemas import LaborPulseQueryRequest

    req = LaborPulseQueryRequest(question="  what skills are trending?  ")
    assert req.question == "what skills are trending?"


def test_laborpulse_query_request_rejects_blank_after_strip() -> None:
    from analytics.api.schemas import LaborPulseQueryRequest

    with pytest.raises(ValidationError):
        LaborPulseQueryRequest(question="   ")


def test_laborpulse_query_request_accepts_valid_uuid_conversation_id() -> None:
    from analytics.api.schemas import LaborPulseQueryRequest

    req = LaborPulseQueryRequest(
        question="any question",
        conversation_id="550e8400-e29b-41d4-a716-446655440000",
    )
    assert req.conversation_id == "550e8400-e29b-41d4-a716-446655440000"


def test_laborpulse_query_request_accepts_null_conversation_id() -> None:
    from analytics.api.schemas import LaborPulseQueryRequest

    req = LaborPulseQueryRequest(question="any question", conversation_id=None)
    assert req.conversation_id is None


def test_laborpulse_query_request_rejects_non_uuid_conversation_id() -> None:
    from analytics.api.schemas import LaborPulseQueryRequest

    with pytest.raises(ValidationError):
        LaborPulseQueryRequest(question="any question", conversation_id="not-a-uuid")
