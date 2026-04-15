"""Tests for analytics QnA truth layer (evidence + policy)."""

from __future__ import annotations

from analytics.query_engine.evidence import build_evidence_bundle
from analytics.query_engine.fixtures import sample_query_request, sample_query_result_payload_ok
from analytics.query_engine.schemas import DataSufficiency, QueryResultPayload


def test_build_evidence_bundle_refuses_zero_rows() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="aggregate_salary",
        classification_confidence=0.82,
        columns=["median_salary", "posting_count"],
        rows=[],
        row_count_returned=0,
        tables_referenced=["job_postings"],
    )

    bundle = build_evidence_bundle(payload)

    assert bundle.sufficiency == DataSufficiency.NO_DATA
    assert bundle.refuse_synthesis is True
    assert bundle.refusal_reason == "No data in scope for the selected filters."
    assert bundle.volume_posting_count == 0
    assert bundle.period_coverage == "period unknown"
    assert bundle.blended_confidence == 0.0
    assert "No rows matched the current filters" in bundle.confidence_explanation


def test_build_evidence_bundle_marks_sparse_low_volume() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="aggregate_salary",
        classification_confidence=0.82,
        columns=["median_salary", "posting_count", "time_period"],
        rows=[{"median_salary": 68000, "posting_count": 12, "time_period": "2025-Q1"}],
        row_count_returned=1,
        tables_referenced=["job_postings"],
    )

    bundle = build_evidence_bundle(payload)

    assert bundle.sufficiency == DataSufficiency.SPARSE
    assert bundle.refuse_synthesis is False
    assert bundle.volume_posting_count == 12
    assert bundle.period_coverage == "2025-Q1"
    assert bundle.facts[0].supporting_count == 12
    assert bundle.facts[0].time_period == "2025-Q1"
    assert "12 postings" in bundle.confidence_explanation


def test_build_evidence_bundle_caps_low_classifier_confidence() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="aggregate_salary",
        classification_confidence=0.55,
        columns=["median_salary", "posting_count", "time_period"],
        rows=[{"median_salary": 72000, "posting_count": 84, "time_period": "2025-Q1"}],
        row_count_returned=1,
        tables_referenced=["job_postings"],
    )

    bundle = build_evidence_bundle(payload)

    assert bundle.refuse_synthesis is False
    assert bundle.blended_confidence <= 0.55
    assert "Classification confidence is below the 0.6 transparency threshold." in bundle.confidence_explanation


def test_build_evidence_bundle_adds_truncation_fact_and_partial_period_note() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="trend_compare",
        classification_confidence=0.84,
        columns=["region", "posting_count", "time_period"],
        rows=[
            {"region": "El Paso", "posting_count": 20, "time_period": "2025-Q1"},
            {"region": "Doña Ana", "posting_count": 15},
        ],
        row_count_returned=2,
        result_truncated=True,
        tables_referenced=["analytics_aggregates"],
    )

    bundle = build_evidence_bundle(payload)

    assert bundle.refuse_synthesis is False
    # Multi-row: max per-row count (conservative; sum would double-count overlapping postings).
    assert bundle.volume_posting_count == 20
    assert bundle.period_coverage == "2025-Q1 (partial period coverage)"
    assert "partial period coverage" in bundle.confidence_explanation
    assert "truncated the result set to 2 rows" in bundle.confidence_explanation
    assert bundle.facts[-1].summary.startswith("Query results were truncated by SQL guardrails")


def test_build_evidence_bundle_refuses_when_salary_metric_missing() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="aggregate_salary",
        classification_confidence=0.82,
        columns=["median_salary", "posting_count", "time_period"],
        rows=[{"posting_count": 22, "time_period": "2025-Q1"}],
        row_count_returned=1,
        tables_referenced=["job_postings"],
    )

    bundle = build_evidence_bundle(payload)

    assert bundle.refuse_synthesis is True
    assert bundle.sufficiency == DataSufficiency.SPARSE
    assert bundle.refusal_reason == "Query results did not include salary metrics required for a salary answer."
    assert "median_salary" in bundle.confidence_explanation


def test_build_evidence_bundle_refuses_router_errors() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="aggregate_salary",
        classification_confidence=0.82,
        router_error="SQL validation failed for non-SELECT statement.",
    )

    bundle = build_evidence_bundle(payload)

    assert bundle.refuse_synthesis is True
    assert bundle.sufficiency == DataSufficiency.NO_DATA
    assert bundle.blended_confidence == 0.0
    assert "SQL validation failed for non-SELECT statement." in (bundle.refusal_reason or "")


def test_distinct_posting_count_overrides_volume_heuristic() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="trend_compare",
        classification_confidence=0.9,
        columns=["posting_count", "time_period"],
        rows=[
            {"posting_count": 20, "time_period": "2025-Q1"},
            {"posting_count": 20, "time_period": "2025-Q2"},
        ],
        row_count_returned=2,
        tables_referenced=["job_postings"],
        distinct_posting_count=25,
    )
    bundle = build_evidence_bundle(payload)
    assert bundle.volume_posting_count == 25


def test_build_evidence_bundle_builds_citation_for_sample_payload() -> None:
    bundle = build_evidence_bundle(sample_query_result_payload_ok())

    assert bundle.refuse_synthesis is False
    assert bundle.sufficiency == DataSufficiency.ADEQUATE
    assert bundle.period_coverage == "2025-Q1"
    assert bundle.volume_posting_count == 84
    assert len(bundle.facts) == 1
    assert bundle.facts[0].summary == "median salary=72,000; posting count=84; time period=2025-Q1."
