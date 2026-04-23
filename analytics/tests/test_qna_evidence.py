"""Tests for analytics QnA truth layer (evidence + policy)."""

from __future__ import annotations

import pytest

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
    assert bundle.sql_execution_error_detail is None


def test_build_evidence_bundle_router_error_appends_postgres_detail() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="aggregate_salary",
        classification_confidence=0.82,
        router_error="sql_execution_failed:ProgrammingError",
        sql_execution_error_detail='column "nope" does not exist',
    )
    bundle = build_evidence_bundle(payload)
    assert bundle.sql_execution_error_detail == 'column "nope" does not exist'
    assert "PostgreSQL:" in (bundle.refusal_reason or "")
    assert 'column "nope" does not exist' in (bundle.refusal_reason or "")


@pytest.mark.parametrize("period_key", ["week_start", "velocity_week"])
def test_build_evidence_bundle_recognizes_router_period_fields(period_key: str) -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="trend_compare",
        classification_confidence=0.9,
        columns=[period_key, "posting_count"],
        rows=[{period_key: "2025-04-21", "posting_count": 42}],
        row_count_returned=1,
        tables_referenced=["skill_demand_weekly"],
    )

    bundle = build_evidence_bundle(payload)

    assert bundle.period_coverage == "2025-04-21"
    assert bundle.facts[0].time_period == "2025-04-21"
    assert "explicit time period" not in bundle.confidence_explanation


def test_build_evidence_bundle_orders_multi_period_ranges_oldest_to_newest() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="trend_compare",
        classification_confidence=0.9,
        columns=["time_period", "posting_count"],
        rows=[
            {"time_period": "2025-W05", "posting_count": 10},
            {"time_period": "2025-W04", "posting_count": 9},
            {"time_period": "2025-W03", "posting_count": 8},
            {"time_period": "2025-W02", "posting_count": 7},
            {"time_period": "2025-W01", "posting_count": 6},
        ],
        row_count_returned=5,
        tables_referenced=["analytics_aggregates"],
    )

    bundle = build_evidence_bundle(payload)

    assert bundle.period_coverage == "2025-W01 to 2025-W05 (5 periods)"


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


def test_citation_summary_structured_salary_omits_currency_when_null() -> None:
    """salary_min/max without salary_currency must not produce 'null USD' or bare 'null'."""
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="employer",
        classification_confidence=0.9,
        columns=[
            "job_title",
            "salary_min",
            "salary_max",
            "salary_currency",
            "posting_count",
        ],
        rows=[
            {
                "job_title": "Software Engineer",
                "salary_min": 50000,
                "salary_max": 90000,
                "salary_currency": None,
                "posting_count": 1,
            }
        ],
        row_count_returned=1,
        tables_referenced=["job_postings"],
    )
    bundle = build_evidence_bundle(payload)
    summary = bundle.facts[0].summary
    assert "salary=50,000–90,000" in summary
    assert "null" not in summary.lower()
    assert "USD" not in summary


def test_citation_summary_structured_salary_includes_iso_currency_when_set() -> None:
    payload = QueryResultPayload(
        request=sample_query_request(),
        intent_label="employer",
        classification_confidence=0.9,
        columns=["salary_min", "salary_max", "salary_currency", "posting_count"],
        rows=[
            {
                "salary_min": 120000,
                "salary_max": 160000,
                "salary_currency": "USD",
                "posting_count": 3,
            }
        ],
        row_count_returned=1,
        tables_referenced=["job_postings"],
    )
    bundle = build_evidence_bundle(payload)
    assert bundle.facts[0].summary.startswith("salary=120,000–160,000 USD")
