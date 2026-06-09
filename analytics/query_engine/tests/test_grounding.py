"""Tests for numeric grounding against EvidenceBundle."""

from __future__ import annotations

from analytics.query_engine.grounding import _build_allowed_corpus, verify_answer_grounding
from analytics.query_engine.schemas import DataSufficiency, EvidenceBundle, EvidenceCitation


def _bundle_with_table_source() -> EvidenceBundle:
    return EvidenceBundle(
        facts=[
            EvidenceCitation(
                citation_id="c1",
                summary="Python skill demand: 42 postings in Borderplex last 90 days",
                source_table="skill_demand_weekly",
                supporting_count=42,
                time_period="last 90 days",
            )
        ],
        period_coverage="last 90 days",
        sufficiency=DataSufficiency.ADEQUATE,
    )


def test_allowed_corpus_excludes_source_table() -> None:
    corpus = _build_allowed_corpus(_bundle_with_table_source())

    assert "skill_demand_weekly" not in corpus
    assert "42" in corpus
    assert "python" in corpus


def test_grounding_accepts_summary_numbers() -> None:
    bundle = _bundle_with_table_source()
    result = verify_answer_grounding("Python appears in 42 postings.", bundle)

    assert result.ok is True
    assert result.unsupported_tokens == ()


def test_grounding_rejects_hallucinated_numbers() -> None:
    bundle = _bundle_with_table_source()
    result = verify_answer_grounding("Python appears in 9,999 postings.", bundle)

    assert result.ok is False
    assert result.reason_code == "numeric_not_in_evidence"
    assert result.unsupported_tokens
