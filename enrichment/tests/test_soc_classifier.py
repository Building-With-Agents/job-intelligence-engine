"""Tests for SOC classification helpers and the batch-level unclassified-rate check.

Coverage:
- ``_resolve_llm_pick_with_reason`` — the resolution logic inside soc_classifier.py.
- ``classify_soc`` end-to-end with a mocked LLM — verifies log.warning fires when the
  LLM returns a response that does not match any candidate code.
- ``_check_soc_unclassified_rate`` — the batch-level fail-safe that emits
  EnrichmentDegraded when more than ``SOC_UNCLASSIFIED_RATE_THRESHOLD`` of enriched
  records are unclassified.

No real DB connection or LLM call is made; all external dependencies are mocked.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from enrichment.classifiers.soc_classifier import _resolve_llm_pick_with_reason

# ---------------------------------------------------------------------------
# _resolve_llm_pick_with_reason
# ---------------------------------------------------------------------------


def test_resolve_exact_code_match() -> None:
    codes = {"15-1252", "11-9021"}
    picked, reason = _resolve_llm_pick_with_reason("15-1252", codes)
    assert picked == "15-1252"
    assert reason == "exact_code_match"


def test_resolve_exact_after_strip_quotes() -> None:
    codes = {"15-1252"}
    picked, reason = _resolve_llm_pick_with_reason("`15-1252`", codes)
    assert picked == "15-1252"
    assert reason == "exact_after_strip_quotes"


def test_resolve_digit_normalized_match() -> None:
    codes = {"15-1252"}
    picked, reason = _resolve_llm_pick_with_reason("151252", codes)
    assert picked == "15-1252"
    assert reason == "digit_normalized_match"


def test_resolve_empty_response_is_unclassified() -> None:
    picked, reason = _resolve_llm_pick_with_reason("", {"15-1252"})
    assert picked == "unclassified"
    assert reason == "empty_llm_response"


def test_resolve_llm_said_unclassified() -> None:
    picked, reason = _resolve_llm_pick_with_reason("unclassified", {"15-1252"})
    assert picked == "unclassified"
    assert reason == "llm_said_unclassified"


def test_resolve_no_candidate_matched() -> None:
    picked, reason = _resolve_llm_pick_with_reason("99-9999", {"15-1252"})
    assert picked == "unclassified"
    assert reason == "no_candidate_matched_llm_output"


def test_resolve_ambiguous_multiple_codes_in_response() -> None:
    codes = {"15-1252", "11-9021"}
    # Both codes appear literally in the response — ambiguous.
    picked, reason = _resolve_llm_pick_with_reason("15-1252 or 11-9021", codes)
    assert picked == "unclassified"
    assert reason == "ambiguous_multiple_catalog_codes_in_response"


def test_resolve_code_embedded_in_prose() -> None:
    """A code buried in prose text should resolve successfully via any matching strategy."""
    codes = {"15-1252"}
    picked, reason = _resolve_llm_pick_with_reason("The best match is code 15-1252.", codes)
    assert picked == "15-1252"
    # The resolver may match via digit-normalisation, substring, or regex — all are correct.
    assert reason in (
        "digit_normalized_match",
        "single_code_substring_of_response",
        "regex_hyphenated_code_in_candidate_set",
    )


# ---------------------------------------------------------------------------
# classify_soc — end-to-end with mocked LLM (log-level assertions)
# ---------------------------------------------------------------------------


from enrichment.classifiers.soc_classifier import classify_soc  # noqa: E402

_FAKE_CANDIDATES = [
    {"code": "15-1252", "title": "Software Developers"},
    {"code": "11-9021", "title": "Computer and Information Systems Managers"},
]


def test_classify_soc_non_candidate_response_logs_warning() -> None:
    """When the LLM returns a code not in the candidate set, log.warning fires.

    This is the key regression guard for the silent-fallback fix: before the
    change, an unclassified outcome only emitted log.info.  Now it must emit
    log.warning with event ``soc_classifier_llm_resolution``.
    """
    with (
        patch(
            "enrichment.classifiers.soc_classifier.get_soc_candidates",
            new=AsyncMock(return_value=_FAKE_CANDIDATES),
        ),
        structlog.testing.capture_logs() as cap,
    ):
        result = asyncio.run(
            classify_soc(
                title="Software Engineer",
                description="Build and ship software",
                session=None,  # mocked above — session is never accessed
                llm=lambda _prompt: "INVALID_CODE",
            )
        )

    assert result == "unclassified"

    warning_logs = [
        e for e in cap if e.get("log_level") == "warning" and e.get("event") == "soc_classifier_llm_resolution"
    ]
    assert len(warning_logs) == 1, (
        f"Expected exactly one warning-level soc_classifier_llm_resolution log; "
        f"got {len(warning_logs)}.  Full captured logs: {cap}"
    )
    assert warning_logs[0]["picked_after_resolve"] == "unclassified"
    assert warning_logs[0]["resolution_reason"] == "no_candidate_matched_llm_output"


def test_classify_soc_valid_response_does_not_log_warning() -> None:
    """A successful classification must NOT emit log.warning for the resolution event.

    Regression guard: we must not produce spurious warnings when classification works.
    """
    with (
        patch(
            "enrichment.classifiers.soc_classifier.get_soc_candidates",
            new=AsyncMock(return_value=_FAKE_CANDIDATES),
        ),
        structlog.testing.capture_logs() as cap,
    ):
        result = asyncio.run(
            classify_soc(
                title="Software Engineer",
                description="Build and ship software",
                session=None,
                llm=lambda _prompt: "15-1252",
            )
        )

    assert result == "15-1252"

    # The resolution event must be info-level (not warning) on a successful pick.
    resolution_warnings = [
        e for e in cap if e.get("log_level") == "warning" and e.get("event") == "soc_classifier_llm_resolution"
    ]
    assert resolution_warnings == [], (
        f"Unexpected warning-level resolution log on successful pick: {resolution_warnings}"
    )


def test_classify_soc_no_candidates_returns_unclassified_without_resolution_warning() -> None:
    """When get_soc_candidates returns an empty list, classify_soc returns 'unclassified'
    immediately (before the LLM is even called) and emits no resolution warning.
    """
    with (
        patch(
            "enrichment.classifiers.soc_classifier.get_soc_candidates",
            new=AsyncMock(return_value=[]),
        ),
        structlog.testing.capture_logs() as cap,
    ):
        result = asyncio.run(
            classify_soc(
                title="Completely Unknown Role XYZ",
                description="",
                session=None,
                llm=lambda _prompt: "15-1252",  # should never be called
            )
        )

    assert result == "unclassified"
    resolution_warnings = [
        e for e in cap if e.get("log_level") == "warning" and e.get("event") == "soc_classifier_llm_resolution"
    ]
    assert resolution_warnings == [], "No resolution warning expected when there were no candidates to begin with."


# ---------------------------------------------------------------------------
# _check_soc_unclassified_rate — batch-level fail-safe
# ---------------------------------------------------------------------------


from enrichment.agent import _check_soc_unclassified_rate  # noqa: E402


@pytest.fixture
def mock_alert_bus():
    """Inject a mock bus into enrichment.agent._alert_bus."""
    bus = MagicMock()
    with patch("enrichment.agent._alert_bus", bus):
        yield bus


def test_rate_check_below_threshold_no_alert(mock_alert_bus: MagicMock) -> None:
    """When unclassified rate is within threshold, no alert is emitted."""
    with patch.dict(os.environ, {"SOC_UNCLASSIFIED_RATE_THRESHOLD": "0.10"}):
        _check_soc_unclassified_rate(
            soc_classified_count=95,
            enriched_count=100,
            correlation_id="corr-1",
            batch_id="batch-1",
            triggered_by_event_type="SkillsExtracted",
        )
    mock_alert_bus.publish.assert_not_called()


def test_rate_check_exactly_at_threshold_no_alert(mock_alert_bus: MagicMock) -> None:
    """Rate equal to threshold (not exceeding) does not trigger the alert."""
    with patch.dict(os.environ, {"SOC_UNCLASSIFIED_RATE_THRESHOLD": "0.10"}):
        _check_soc_unclassified_rate(
            soc_classified_count=90,
            enriched_count=100,
            correlation_id="corr-2",
            batch_id="batch-2",
            triggered_by_event_type="SkillsExtracted",
        )
    mock_alert_bus.publish.assert_not_called()


def test_rate_check_above_threshold_emits_alert(mock_alert_bus: MagicMock) -> None:
    """When rate exceeds threshold the alert bus receives an EnrichmentDegraded event."""
    with patch.dict(os.environ, {"SOC_UNCLASSIFIED_RATE_THRESHOLD": "0.10"}):
        _check_soc_unclassified_rate(
            soc_classified_count=80,  # 20 unclassified out of 100 = 20 %
            enriched_count=100,
            correlation_id="corr-3",
            batch_id="batch-3",
            triggered_by_event_type="SkillsExtracted",
        )

    mock_alert_bus.publish.assert_called_once()
    published_event = mock_alert_bus.publish.call_args[0][0]
    payload = published_event.payload
    assert payload["event_type"] == "EnrichmentDegraded"
    assert payload["classifier"] == "soc"
    assert payload["reason"] == "unclassified_rate_exceeded"
    assert payload["unclassified_count"] == 20
    assert payload["unclassified_rate"] == pytest.approx(0.20, abs=0.001)


def test_rate_check_zero_enriched_no_error(mock_alert_bus: MagicMock) -> None:
    """Zero enriched_count must not raise ZeroDivisionError and must not alert."""
    _check_soc_unclassified_rate(
        soc_classified_count=0,
        enriched_count=0,
        correlation_id="corr-4",
        batch_id="batch-4",
        triggered_by_event_type="SkillsExtracted",
    )
    mock_alert_bus.publish.assert_not_called()


def test_rate_check_no_bus_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the alert bus is not registered the check logs a warning but does not raise."""
    monkeypatch.setattr("enrichment.agent._alert_bus", None)
    with patch.dict(os.environ, {"SOC_UNCLASSIFIED_RATE_THRESHOLD": "0.10"}):
        # 100 % unclassified — well above threshold, but no bus registered.
        _check_soc_unclassified_rate(
            soc_classified_count=0,
            enriched_count=50,
            correlation_id="corr-5",
            batch_id="batch-5",
            triggered_by_event_type="SkillsExtracted",
        )
    # No exception — just a warning log; nothing else to assert here.


def test_rate_check_all_classified_no_alert(mock_alert_bus: MagicMock) -> None:
    """Perfect classification rate must not alert even if threshold is very tight."""
    with patch.dict(os.environ, {"SOC_UNCLASSIFIED_RATE_THRESHOLD": "0.01"}):
        _check_soc_unclassified_rate(
            soc_classified_count=100,
            enriched_count=100,
            correlation_id="corr-6",
            batch_id="batch-6",
            triggered_by_event_type="SkillsExtracted",
        )
    mock_alert_bus.publish.assert_not_called()


def test_rate_check_bus_publish_error_does_not_raise(mock_alert_bus: MagicMock) -> None:
    """If the bus raises, _check_soc_unclassified_rate swallows it and logs a warning."""
    mock_alert_bus.publish.side_effect = RuntimeError("bus unavailable")
    with patch.dict(os.environ, {"SOC_UNCLASSIFIED_RATE_THRESHOLD": "0.10"}):
        # Should not propagate the RuntimeError.
        _check_soc_unclassified_rate(
            soc_classified_count=0,
            enriched_count=100,
            correlation_id="corr-7",
            batch_id="batch-7",
            triggered_by_event_type="SkillsExtracted",
        )
    mock_alert_bus.publish.assert_called_once()
