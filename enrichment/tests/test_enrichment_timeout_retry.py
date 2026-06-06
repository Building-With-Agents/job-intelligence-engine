"""Tests for JIE #150 — enrichment timeout retry + quarantine.

Validates:
1. ``enrich_record_async(raise_on_timeout=False)`` preserves pre-#150 behavior
   (TimeoutError stuffed into result slots; merged dict returned).
2. ``enrich_record_async(raise_on_timeout=True)`` raises EnrichmentTimeoutError
   on gather timeout, exposing attempt metadata for the caller.
3. ``_quarantine_enrichment_record`` writes to dbo.enrichment_quarantine
   when ``normalized_job_id`` is supplied.
4. ``_quarantine_enrichment_record`` is a no-op (with WARN log) when
   ``normalized_job_id`` is None.
5. ``EnrichmentQuarantine`` model can be instantiated with required fields.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def test_enrichment_timeout_error_carries_metadata() -> None:
    """The exception carries attempt-count and per-attempt timing."""
    from enrichment.agent import EnrichmentTimeoutError

    exc = EnrichmentTimeoutError(
        normalized_job_id=42,
        timeout_seconds=120,
        attempts=1,
        elapsed_ms_per_attempt=[121_300],
        error_summary="gather timeout after 120s",
    )
    assert exc.normalized_job_id == 42
    assert exc.timeout_seconds == 120
    assert exc.attempts == 1
    assert exc.elapsed_ms_per_attempt == [121_300]
    assert "timed out after 1 attempt" in str(exc)


def test_enrichment_quarantine_model_fields() -> None:
    """The ORM class accepts the documented fields without errors."""
    from common.data_store.models import EnrichmentQuarantine

    record = EnrichmentQuarantine(
        normalized_job_id=100,
        reason="timeout",
        attempt_count=2,
        timeout_seconds=120,
        elapsed_ms_per_attempt=[120_100, 120_200],
        error_summary="gather timeout",
    )
    assert record.normalized_job_id == 100
    assert record.reason == "timeout"
    assert record.attempt_count == 2
    assert record.elapsed_ms_per_attempt == [120_100, 120_200]


def test_quarantine_skipped_when_normalized_job_id_is_none() -> None:
    """When the job has no normalized_job_id, the function logs and returns."""
    from enrichment.agent import _quarantine_enrichment_record

    session = MagicMock()
    _quarantine_enrichment_record(
        session,
        normalized_job_id=None,
        reason="timeout",
        attempt_count=2,
        timeout_seconds=120,
        elapsed_ms_per_attempt=[120_100],
        error_summary="gather timeout",
    )
    # No SQL operation attempted
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_quarantine_persists_when_normalized_job_id_set() -> None:
    """When normalized_job_id is given, an EnrichmentQuarantine row is added."""
    from enrichment.agent import _quarantine_enrichment_record

    session = MagicMock()
    _quarantine_enrichment_record(
        session,
        normalized_job_id=42,
        reason="timeout",
        attempt_count=2,
        timeout_seconds=120,
        elapsed_ms_per_attempt=[120_100, 120_200],
        error_summary="gather timeout after 120s",
    )
    session.add.assert_called_once()
    session.commit.assert_called_once()
    record_arg = session.add.call_args.args[0]
    assert record_arg.normalized_job_id == 42
    assert record_arg.reason == "timeout"
    assert record_arg.attempt_count == 2
    assert record_arg.timeout_seconds == 120


# -- raise_on_timeout behavior -------------------------------------------


class _FakeAgent:
    """Stub mirroring the parts of EnrichmentAgent we need for the timeout path."""

    agent_id = "enrichment-agent-test"

    def __init__(self) -> None:
        self._external_facade = MagicMock()

        async def _no_external(*args: Any, **kwargs: Any) -> dict:
            return {}

        self._external_facade.fetch_for_posting = _no_external

    def _ensure_refs(self) -> tuple[list, list]:
        return ([], [])


def _make_agent_stub():
    """Create a stub agent with the real enrich_record_async bound."""
    from enrichment.agent import EnrichmentAgent

    stub = _FakeAgent()
    # Bind the unbound method onto our stub
    stub.enrich_record_async = EnrichmentAgent.enrich_record_async.__get__(stub, _FakeAgent)
    return stub


def test_raise_on_timeout_true_propagates_enrichment_timeout_error() -> None:
    """When raise_on_timeout=True, gather TimeoutError → EnrichmentTimeoutError."""
    from enrichment.agent import EnrichmentTimeoutError

    stub = _make_agent_stub()

    # Patch the resolvers and the gather wait_for to force a timeout.
    with (
        patch("enrichment.agent.resolve_company", return_value=(None, 0.0)),
        patch(
            "enrichment.agent.resolve_location",
            return_value=(None, 0.0, "", None),
        ),
        patch("enrichment.agent.compute_field_confidence", return_value={}),
        patch("enrichment.agent.compute_overall_confidence", return_value=0.0),
        patch("enrichment.agent.asyncio.wait_for", side_effect=TimeoutError()),
        patch("enrichment._config.enrichment_llm_timeout_seconds", return_value=120),
    ):
        posting = {
            "title": "Test job",
            "description": "Test",
            "company": "TestCo",
            "location": "Anywhere",
            "normalized_job_id": 99,
        }
        # Need a non-None session so the LLM-call block executes
        with pytest.raises(EnrichmentTimeoutError) as excinfo:
            asyncio.run(stub.enrich_record_async(posting, session=MagicMock(), raise_on_timeout=True))

    assert excinfo.value.normalized_job_id == 99
    assert excinfo.value.timeout_seconds == 120
    assert excinfo.value.attempts == 1


def test_raise_on_timeout_false_preserves_pre_150_behavior() -> None:
    """Default ``raise_on_timeout=False`` → degraded merged dict (no raise)."""
    stub = _make_agent_stub()

    with (
        patch("enrichment.agent.resolve_company", return_value=(None, 0.0)),
        patch(
            "enrichment.agent.resolve_location",
            return_value=(None, 0.0, "", None),
        ),
        patch("enrichment.agent.compute_field_confidence", return_value={}),
        patch("enrichment.agent.compute_overall_confidence", return_value=0.0),
        patch("enrichment.agent.asyncio.wait_for", side_effect=TimeoutError()),
        patch("enrichment._config.enrichment_llm_timeout_seconds", return_value=120),
        # classify_job is called late in the function — stub it to avoid extra work
        patch("enrichment.agent.classify_job", return_value=(None, None)),
        # build_extraction_dict already imported as builtin; stub it
        patch(
            "scripts.jsearch_enrichment_preview_lib.build_extraction_dict",
            return_value={},
        ),
    ):
        posting = {
            "title": "Test job",
            "description": "Test",
            "company": "TestCo",
            "location": "Anywhere",
            "normalized_job_id": 99,
        }
        merged = asyncio.run(stub.enrich_record_async(posting, session=MagicMock(), raise_on_timeout=False))

    # Function returns successfully with degraded fields (no exception).
    # On the timeout path naics_result is a TimeoutError, so the agent falls back
    # to posting.get("naics_code") — None here, since the test posting carries no
    # naics_code. This is the pre-#150 degraded contract we must preserve.
    assert merged is not None
    assert isinstance(merged, dict)
    assert merged.get("naics_code") is None


def test_enrichment_max_retries_config_accessor() -> None:
    """The config accessor returns the YAML default (1)."""
    from enrichment._config import enrichment_max_retries

    # Bust the cache to read fresh
    enrichment_max_retries.cache_clear()
    val = enrichment_max_retries()
    assert val >= 0
    assert isinstance(val, int)


# -- batch aggregation excludes quarantined records ----------------------


def test_quarantined_results_excluded_from_batch_metrics() -> None:
    """JIE #150 regression: ``__quarantined`` results must not inflate metrics.

    A timed-out record returned as ``{"__quarantined": True, ...}`` by the
    parallel batch loop must be counted under ``quarantined_count`` and excluded
    from ``enriched_count`` (and the distributions / SOC denominator). Otherwise
    a record that was held back from ``job_postings`` would still be reported as
    successfully enriched in the RecordEnriched event and downstream analytics.

    Mocks ``_enrich_batch_parallel_bridge`` to return one genuinely enriched
    result plus one quarantined result, then asserts the aggregated event payload.
    """
    from common.event_envelope import EventEnvelope
    from enrichment.agent import EnrichmentAgent

    enriched_posting = {
        "posting_id": 1,
        "title": "Senior Python Engineer",
        "company": "Acme Corp",
        "source": "jsearch",
        "normalized_job_id": 1,
    }
    quarantined_posting = {
        "posting_id": 2,
        "title": "Data Engineer",
        "company": "Globex",
        "source": "jsearch",
        "normalized_job_id": 2,
    }
    bridge_results = [
        {
            "spam_score": 0.05,
            "spam_tier": "clean",
            "overall_confidence": 0.9,
            "field_confidence": {},
            "soc_code": "15-1252",
            "naics_code": "541511",
            "quality_score": 0.85,
            "quality_components": {},
            "__posting": enriched_posting,
            "__row": {},
        },
        # JIE #150: a record that timed out on every attempt and was quarantined.
        {"__quarantined": True, "__posting": quarantined_posting, "__row": {}},
    ]

    with (
        patch.object(EnrichmentAgent, "_enrichment_parallel_enabled", return_value=True),
        patch.object(EnrichmentAgent, "_enrich_batch_parallel_bridge", return_value=bridge_results),
        patch("common.llm_adapter.get_tracer", return_value=None),
    ):
        agent = EnrichmentAgent()
        event = EventEnvelope(
            correlation_id="cid-quarantine",
            agent_id="skills-extraction",
            payload={
                "batch_id": "batch-quarantine-test",
                "records": [
                    {"posting_id": 1, "title": "Senior Python Engineer", "company": "Acme Corp"},
                    {"posting_id": 2, "title": "Data Engineer", "company": "Globex"},
                ],
            },
        )
        out = agent.process(event)

    # Only the genuinely enriched record counts; the quarantined one is held out.
    assert out.payload["enriched_count"] == 1
    assert out.payload["quarantined_count"] == 1
