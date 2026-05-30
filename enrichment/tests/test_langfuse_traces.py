"""Tests for per-record and batch-level Langfuse observability traces (JIE #15).

Acceptance criteria:
- Per-record: quality_score, spam_score, overall_confidence, field_confidence visible in Langfuse job span
- Per-record: quality component breakdown emitted
- Per-batch: spam tier distribution counts emitted
- Per-batch: SOC/NAICS unclassified gap metrics emitted
- No PII in any logged values (job titles and company names are fine; no personal data)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from common.event_envelope import EventEnvelope
from enrichment.agent import EnrichmentAgent


def _base_payload(**row_overrides: object) -> dict:
    row: dict = {
        "posting_id": 42,
        "title": "Senior Python Engineer",
        "company": "Acme Corp",
        "quality_score": 0.8,
        "spam_score": 0.2,
        "seniority": "senior",
        "role_classification": "Software Engineering",
        "skills": [],
    }
    row.update({k: v for k, v in row_overrides.items() if k != "batch_id"})
    batch_id = row_overrides.get("batch_id", "batch-trace-test")
    return {"batch_id": batch_id, "records": [row]}


@patch.object(EnrichmentAgent, "_enrichment_parallel_enabled", return_value=False)
@patch("enrichment.agent.resolve_sector", return_value=None)
@patch.object(EnrichmentAgent, "enrich_record")
@patch("common.llm_adapter.get_tracer")
def test_per_record_quality_spam_event_emitted(
    mock_get_tracer: MagicMock,
    mock_enrich: MagicMock,
    _mock_sector: MagicMock,
    _mock_parallel: MagicMock,
) -> None:
    """tracer.log_event('enrichment_record_quality_spam', ...) must be called once per
    non-spam record with all required quality/spam/confidence keys (JIE #15)."""
    mock_tracer = MagicMock()
    mock_get_tracer.return_value = mock_tracer

    mock_enrich.return_value = {
        "spam_score": 0.12,
        "spam_tier": "clean",
        "overall_confidence": 0.81,
        "field_confidence": {"title": 0.95, "description": 0.72},
        "soc_code": "15-1252",
        "naics_code": "541511",
        "role_classification": "Software Engineering",
        "seniority": "senior",
    }

    agent = EnrichmentAgent()
    event = EventEnvelope(
        correlation_id="cid-trace",
        agent_id="skills-extraction",
        payload=_base_payload(is_spam=False),
    )
    out = agent.process(event)

    assert out.payload["enriched_count"] == 1

    # Collect all log_event calls for the per-record event name
    record_calls = [c for c in mock_tracer.log_event.call_args_list if c.args[0] == "enrichment_record_quality_spam"]
    assert len(record_calls) == 1, (
        f"Expected exactly 1 'enrichment_record_quality_spam' log_event call; got {len(record_calls)}. "
        f"All calls: {[c.args[0] for c in mock_tracer.log_event.call_args_list]}"
    )

    traced = record_calls[0].args[1]

    # Required per-record keys (JIE #15 acceptance criteria)
    for key in ("quality_score", "spam_score", "overall_confidence", "field_confidence"):
        assert key in traced, f"Missing required key {key!r} in enrichment_record_quality_spam trace"

    # Quality components must also be present
    assert "quality_components" in traced, "quality_components must be emitted per JIE #15"

    # Spam metrics must match what enrich_record returned
    assert traced["spam_score"] == 0.12
    assert traced["spam_tier"] == "clean"
    assert traced["overall_confidence"] == 0.81
    assert traced["soc_code"] == "15-1252"
    assert traced["naics_code"] == "541511"

    # PII check: field_confidence is a dict of field names → floats, no personal data
    assert isinstance(traced["field_confidence"], dict)
    for v in traced["field_confidence"].values():
        assert isinstance(v, (int, float)), "field_confidence values must be numeric (no PII strings)"


@patch.object(EnrichmentAgent, "_enrichment_parallel_enabled", return_value=False)
@patch("enrichment.agent.resolve_sector", return_value=None)
@patch.object(EnrichmentAgent, "enrich_record")
@patch("common.llm_adapter.get_tracer")
def test_batch_enrichment_complete_has_spam_tier_distribution(
    mock_get_tracer: MagicMock,
    mock_enrich: MagicMock,
    _mock_sector: MagicMock,
    _mock_parallel: MagicMock,
) -> None:
    """tracer.log_event('enrichment_complete', ...) must include spam_tier_distribution
    and gap metrics for SOC/NAICS (JIE #15 batch-level acceptance criteria)."""
    mock_tracer = MagicMock()
    mock_get_tracer.return_value = mock_tracer

    mock_enrich.return_value = {
        "spam_score": 0.05,
        "spam_tier": "clean",
        "overall_confidence": 0.9,
        "field_confidence": {},
        "soc_code": "15-1252",
        "naics_code": "541511",
    }

    agent = EnrichmentAgent()
    # One clean + one spam-rejected record in the batch
    batch_payload = {
        "batch_id": "batch-dist-test",
        "records": [
            {
                "posting_id": 1,
                "title": "Engineer",
                "company": "Corp A",
                "skills": [],
                "is_spam": False,
            },
            {
                "posting_id": 2,
                "title": "Manager",
                "company": "Corp B",
                "skills": [],
                "is_spam": True,
            },
        ],
    }
    event = EventEnvelope(
        correlation_id="cid-batch",
        agent_id="skills-extraction",
        payload=batch_payload,
    )
    out = agent.process(event)

    assert out.payload["enriched_count"] == 1
    assert out.payload["spam_rejected_count"] == 1

    complete_calls = [c for c in mock_tracer.log_event.call_args_list if c.args[0] == "enrichment_complete"]
    assert len(complete_calls) == 1, (
        f"Expected exactly 1 'enrichment_complete' log_event call; got {len(complete_calls)}"
    )

    batch_data = complete_calls[0].args[1]

    # spam_tier_distribution is required (JIE #15 batch-level)
    assert "spam_tier_distribution" in batch_data, "spam_tier_distribution missing from enrichment_complete"
    dist = batch_data["spam_tier_distribution"]
    assert "clean" in dist
    assert "flagged" in dist
    assert "rejected" in dist
    assert dist["clean"] == 1, "1 enriched record should map to clean=1"
    assert dist["rejected"] == 1, "1 spam-rejected record should map to rejected=1"

    # SOC/NAICS gap metrics emitted (JIE #15)
    assert "naics_unclassified_count" in batch_data, "naics_unclassified_count missing from enrichment_complete"
    assert "soc_unclassified_count" in batch_data, "soc_unclassified_count missing from enrichment_complete"

    # Distribution dicts present for temporal/borderplex observability
    assert "temporal_period_distribution" in batch_data
    assert "borderplex_subregion_distribution" in batch_data


@patch.object(EnrichmentAgent, "_enrichment_parallel_enabled", return_value=False)
@patch("enrichment.agent.resolve_sector", return_value=None)
@patch.object(EnrichmentAgent, "enrich_record")
@patch("common.llm_adapter.get_tracer")
def test_no_tracer_log_event_when_tracer_is_none(
    mock_get_tracer: MagicMock,
    mock_enrich: MagicMock,
    _mock_sector: MagicMock,
    _mock_parallel: MagicMock,
) -> None:
    """When get_tracer() returns None (Langfuse disabled), no log_event is called — pipeline must not crash."""
    mock_get_tracer.return_value = None
    mock_enrich.return_value = {"spam_score": 0.1}

    agent = EnrichmentAgent()
    event = EventEnvelope(
        correlation_id="cid-no-tracer",
        agent_id="skills-extraction",
        payload=_base_payload(is_spam=False),
    )
    out = agent.process(event)

    assert out.payload["enriched_count"] == 1
    # No crash, no log_event calls on the None tracer
