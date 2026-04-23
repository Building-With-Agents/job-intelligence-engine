"""Unit tests for JIE #225 LaborPulse wire mapping."""

from __future__ import annotations

import uuid

import pytest

from analytics.api.laborpulse_wire import (
    confidence_bucket,
    resolve_laborpulse_conversation_id,
    to_laborpulse_query_response,
    validate_laborpulse_question,
)
from analytics.api.schemas import AnalyticsQueryResponse, EvidenceItem


def test_resolve_conversation_id_generates_uuid() -> None:
    out = resolve_laborpulse_conversation_id(None)
    uuid.UUID(out)


def test_resolve_conversation_id_echoes_valid() -> None:
    cid = str(uuid.uuid4())
    assert resolve_laborpulse_conversation_id(cid) == cid


def test_resolve_conversation_id_invalid() -> None:
    with pytest.raises(ValueError, match="invalid_conversation_id"):
        resolve_laborpulse_conversation_id("nope")


def test_confidence_bucket_never_mock_string() -> None:
    for score in (0.0, 0.2, 0.59, 0.6, 0.84, 0.85, 1.0):
        b = confidence_bucket(score)
        assert b in ("low", "medium", "high")
        assert b != "mock"


def test_validate_question_empty() -> None:
    with pytest.raises(ValueError, match="empty_question"):
        validate_laborpulse_question("   ")


def test_validate_question_too_short() -> None:
    with pytest.raises(ValueError, match="question_too_short"):
        validate_laborpulse_question("ab")


def test_validate_question_too_broad() -> None:
    with pytest.raises(ValueError, match="question_too_broad"):
        validate_laborpulse_question("show me all data for skills")


def test_to_laborpulse_pads_followups_and_cost() -> None:
    internal = AnalyticsQueryResponse(
        answer="a",
        evidence=[EvidenceItem(title="t", source="s", snippet="x")],
        confidence=0.9,
        follow_up_questions=["only_one"],
        sql_generated="tables: dbo.x | label",
        cost_usd=0.01,
        total_cost_usd=0.05,
    )
    lp = to_laborpulse_query_response(internal, conversation_id=str(uuid.uuid4()))
    assert lp.confidence == "high"
    assert 2 <= len(lp.follow_up_questions) <= 4
    assert lp.follow_up_questions[0] == "only_one"
    assert lp.cost_usd == 0.05
    assert lp.sql_generated.startswith("tables:")
    assert len(lp.evidence) == 1
