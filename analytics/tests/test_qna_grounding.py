"""Unit tests for post-generation numeric grounding (GitHub #117)."""

from __future__ import annotations

from unittest.mock import patch

from analytics.query_engine.fixtures import sample_evidence_bundle_adequate
from analytics.query_engine.grounding import (
    prefix_period_coverage,
    verify_answer_grounding,
)
from analytics.query_engine.synthesis import AGENT_FOLLOWUP, AGENT_SYNTHESIS, synthesize_answer


def test_verify_grounding_accepts_numbers_from_facts() -> None:
    bundle = sample_evidence_bundle_adequate()
    text = "In 2025-Q1, the median salary was about 72,000 USD across 84 postings per the evidence."
    r = verify_answer_grounding(text, bundle)
    assert r.ok
    assert r.unsupported_tokens == ()


def test_verify_grounding_rejects_invented_statistic() -> None:
    bundle = sample_evidence_bundle_adequate()
    text = "The median salary is 999,999 USD based on the data."
    r = verify_answer_grounding(text, bundle)
    assert not r.ok
    assert r.unsupported_tokens


def test_prefix_period_coverage_idempotent() -> None:
    p = "2025-Q1"
    body = f"Summary for {p}."
    assert "Data period:" not in prefix_period_coverage(body, p)
    assert prefix_period_coverage("Short answer only.", p).startswith("Data period:")


def test_synthesis_grounding_fallback_on_hallucination() -> None:
    bundle = sample_evidence_bundle_adequate()
    calls: list[str] = []

    def fake(prompt: str, agent_name: str, **_kwargs):
        calls.append(agent_name)
        if agent_name == AGENT_SYNTHESIS:
            if len([c for c in calls if c == AGENT_SYNTHESIS]) == 1:
                return {
                    "content": "Median compensation reached 500,000 USD immediately.",
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "cost_usd": 0.01,
                    "success": True,
                    "extraction_failed": False,
                    "model": "mock",
                }
            return {
                "content": "Still wrong: 888,888 postings.",
                "input_tokens": 10,
                "output_tokens": 10,
                "cost_usd": 0.01,
                "success": True,
                "extraction_failed": False,
                "model": "mock",
            }
        if agent_name == AGENT_FOLLOWUP:
            return {
                "content": "[]",
                "input_tokens": 1,
                "output_tokens": 1,
                "cost_usd": 0.0,
                "success": True,
                "extraction_failed": False,
                "model": "mock",
            }
        raise AssertionError(agent_name)

    with patch("analytics.query_engine.synthesis.complete", side_effect=fake):
        r = synthesize_answer(
            bundle,
            user_query="Salaries?",
            intent_label="aggregate_salary",
        )
    assert not r.refused
    assert "not fully supported" in r.answer_text.lower() or "citations" in r.answer_text.lower()
    assert "2025-q1" in r.answer_text.lower()
    assert sum(1 for c in calls if c == AGENT_SYNTHESIS) == 2


def test_synthesis_answer_includes_period_after_grounded_ok() -> None:
    bundle = sample_evidence_bundle_adequate()

    def fake(prompt: str, agent_name: str, **_kwargs):
        if agent_name == AGENT_SYNTHESIS:
            return {
                "content": "Median salary USD 72,000 from 84 postings in 2025-Q1.",
                "input_tokens": 10,
                "output_tokens": 10,
                "cost_usd": 0.01,
                "success": True,
                "extraction_failed": False,
                "model": "mock",
            }
        if agent_name == AGENT_FOLLOWUP:
            return {
                "content": '["Next?"]',
                "input_tokens": 1,
                "output_tokens": 1,
                "cost_usd": 0.001,
                "success": True,
                "extraction_failed": False,
                "model": "mock",
            }
        raise AssertionError(agent_name)

    with patch("analytics.query_engine.synthesis.complete", side_effect=fake):
        r = synthesize_answer(
            bundle,
            user_query="q",
            intent_label="aggregate_salary",
        )
    assert "2025-Q1" in r.answer_text
    assert "72,000" in r.answer_text or "72000" in r.answer_text.replace(",", "")
