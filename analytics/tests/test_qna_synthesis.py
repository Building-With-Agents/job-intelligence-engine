"""Tests for analytics QnA synthesis (voice layer). GitHub #117."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from analytics.query_engine.constants import (
    CONFIDENCE_TRANSPARENCY_THRESHOLD,
    VOLUME_WARNING_POSTING_THRESHOLD,
)
from analytics.query_engine.fixtures import (
    sample_evidence_bundle_adequate,
    sample_evidence_bundle_low_confidence,
    sample_evidence_bundle_low_volume,
    sample_evidence_bundle_refused,
    sample_query_result_payload_ok,
)
from analytics.query_engine.qna import run_analytics_qna
from analytics.query_engine.schemas import CostLedger, LLMCallCost
from analytics.query_engine.synthesis import AGENT_FOLLOWUP, AGENT_SYNTHESIS, synthesize_answer


def test_refusal_skips_llm_and_sets_message() -> None:
    with patch("analytics.query_engine.synthesis.complete") as m:
        r = synthesize_answer(
            sample_evidence_bundle_refused(),
            user_query="What is the median?",
            intent_label="aggregate_salary",
        )
        m.assert_not_called()
    assert r.refused is True
    assert r.refusal_message == "Query returned zero rows for the selected filters."
    assert r.answer_text == ""
    assert r.follow_up_questions == []
    assert r.confidence_flagged_low is True
    assert r.volume_flagged_low is True
    assert r.volume_warning is not None


def _ok_synthesis_result(content: str, cost: float = 0.01) -> dict:
    return {
        "content": content,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": cost,
        "success": True,
        "extraction_failed": False,
        "model": "mock-model",
    }


def _failed_result() -> dict:
    return {
        "content": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "success": False,
        "extraction_failed": True,
        "model": None,
    }


def test_flags_low_confidence_from_bundle() -> None:
    bundle = sample_evidence_bundle_low_confidence()
    assert bundle.blended_confidence < CONFIDENCE_TRANSPARENCY_THRESHOLD

    def fake(prompt: str, agent_name: str, **_kwargs):
        if agent_name == AGENT_SYNTHESIS:
            return _ok_synthesis_result("Answer based on limited evidence.")
        if agent_name == AGENT_FOLLOWUP:
            return _ok_synthesis_result('["Next filter?", "Other geography?"]', cost=0.002)
        raise AssertionError(agent_name)

    with patch("analytics.query_engine.synthesis.complete", side_effect=fake):
        r = synthesize_answer(
            bundle,
            user_query="Trend?",
            intent_label="trend_compare",
        )
    assert r.confidence_flagged_low is True
    assert r.confidence_explanation == "Blended confidence is below the transparency threshold."
    assert r.volume_flagged_low is False


def test_flags_low_volume_uses_threshold_constant() -> None:
    bundle = sample_evidence_bundle_low_volume()
    assert bundle.volume_posting_count is not None
    assert bundle.volume_posting_count < VOLUME_WARNING_POSTING_THRESHOLD

    def fake(prompt: str, agent_name: str, **_kwargs):
        if agent_name == AGENT_SYNTHESIS:
            return _ok_synthesis_result("Directional view from small N.")
        if agent_name == AGENT_FOLLOWUP:
            return _ok_synthesis_result('["Drill into sector?", "Compare quarter?"]', cost=0.002)
        raise AssertionError(agent_name)

    with patch("analytics.query_engine.synthesis.complete", side_effect=fake):
        r = synthesize_answer(
            bundle,
            user_query="Salaries?",
            intent_label="aggregate_salary",
        )
    assert r.volume_flagged_low is True
    assert r.volume_warning is not None
    assert str(VOLUME_WARNING_POSTING_THRESHOLD) in r.volume_warning


def test_cost_ledger_merge_and_breakdown() -> None:
    inbound = CostLedger(
        legs=[
            LLMCallCost(
                leg="intent_classification",
                cost_usd=0.001,
                input_tokens=20,
                output_tokens=5,
                model="m1",
            ),
            LLMCallCost(leg="sql_generation", cost_usd=0.002, input_tokens=30, output_tokens=10, model="m2"),
        ]
    )

    def fake(prompt: str, agent_name: str, **_kwargs):
        if agent_name == AGENT_SYNTHESIS:
            return _ok_synthesis_result("Median paraphrased from facts.", cost=0.01)
        if agent_name == AGENT_FOLLOWUP:
            return _ok_synthesis_result(
                '["Skill mix for these roles?", "Wage bands?", "Time window?"]',
                cost=0.02,
            )
        raise AssertionError(agent_name)

    with patch("analytics.query_engine.synthesis.complete", side_effect=fake):
        r = synthesize_answer(
            sample_evidence_bundle_adequate(),
            user_query="Median salary?",
            intent_label="aggregate_salary",
            cost_ledger=inbound,
        )
    assert r.total_cost_usd == pytest.approx(0.033, rel=1e-5)
    assert r.cost_breakdown_usd.get("intent_classification") == pytest.approx(0.001)
    assert r.cost_breakdown_usd.get("sql_generation") == pytest.approx(0.002)
    assert r.cost_breakdown_usd.get("synthesis") == pytest.approx(0.01)
    assert r.cost_breakdown_usd.get("follow_up") == pytest.approx(0.02)
    assert 2 <= len(r.follow_up_questions) <= 3


def test_llm_main_failure_safe_response() -> None:
    def fake(prompt: str, agent_name: str, **_kwargs):
        if agent_name == AGENT_SYNTHESIS:
            return _failed_result()
        raise AssertionError("follow-up should not run")

    with patch("analytics.query_engine.synthesis.complete", side_effect=fake):
        r = synthesize_answer(
            sample_evidence_bundle_adequate(),
            user_query="x",
            intent_label="aggregate_salary",
        )
    assert "could not be generated" in r.answer_text.lower() or "narrative" in r.answer_text.lower()
    assert r.follow_up_questions == []
    assert r.refused is False


def test_periods_and_citations_echo() -> None:
    def fake(prompt: str, agent_name: str, **_kwargs):
        if agent_name == AGENT_SYNTHESIS:
            return _ok_synthesis_result("ok")
        return _ok_synthesis_result("[]", cost=0.0)

    with patch("analytics.query_engine.synthesis.complete", side_effect=fake):
        r = synthesize_answer(
            sample_evidence_bundle_adequate(),
            user_query="q",
            intent_label="aggregate_salary",
        )
    assert r.periods_described == "2025-Q1"
    assert len(r.citations) == 1
    assert r.citations[0].citation_id == "c1"


def test_run_analytics_qna_optional_pipeline() -> None:
    n = {"i": 0}

    def multi(prompt, agent_name, **_k):
        n["i"] += 1
        if n["i"] == 1:
            return _ok_synthesis_result("Answer.")
        return _ok_synthesis_result('["Q1?", "Q2?"]')

    with patch("analytics.query_engine.synthesis.complete", side_effect=multi) as m:
        try:
            out = run_analytics_qna(sample_query_result_payload_ok())
        except NotImplementedError:
            pytest.skip("Dev 1 evidence not merged yet")
    if out.refused:
        pytest.skip("Sample QueryResultPayload refused under real evidence policy")
    assert isinstance(out.answer_text, str)
    assert m.call_count >= 1
