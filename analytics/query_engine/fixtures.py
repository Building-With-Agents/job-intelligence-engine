"""Example payloads for tests and local dev — not production data."""

from __future__ import annotations

from analytics.query_engine.schemas import (
    DataSufficiency,
    EvidenceBundle,
    EvidenceCitation,
    QueryResultPayload,
)
from common.types.query_request import QueryRequest


def sample_query_request() -> QueryRequest:
    return QueryRequest(query="What is median salary for software roles in El Paso?")


def sample_query_result_payload_ok() -> QueryResultPayload:
    return QueryResultPayload(
        request=sample_query_request(),
        intent_label="aggregate_salary",
        classification_confidence=0.82,
        executed_sql="SELECT percentile_disc(0.5) AS median_salary FROM dbo.job_postings WHERE ...",
        columns=["median_salary", "posting_count"],
        rows=[{"median_salary": 72000, "posting_count": 84}],
        row_count_returned=1,
        result_truncated=False,
        tables_referenced=["job_postings"],
        execution_time_ms=12.5,
        correlation_id="test-correlation-001",
    )


def sample_evidence_bundle_adequate() -> EvidenceBundle:
    return EvidenceBundle(
        facts=[
            EvidenceCitation(
                citation_id="c1",
                summary="Median salary USD 72,000 from 84 job postings in scope.",
                source_table="job_postings",
                supporting_count=84,
                time_period="2025-Q1",
            )
        ],
        period_coverage="2025-Q1",
        volume_posting_count=84,
        sufficiency=DataSufficiency.ADEQUATE,
        blended_confidence=0.82,
        confidence_explanation="High intent match and posting count above volume threshold.",
        refuse_synthesis=False,
        refusal_reason=None,
    )
