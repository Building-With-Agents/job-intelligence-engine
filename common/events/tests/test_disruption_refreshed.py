"""Tests for DisruptionRefreshed event builder and typed wrapper."""

from __future__ import annotations

import pytest

from common.events.disruption_refreshed import (
    DisruptionRefreshedPayload,
    build_disruption_refreshed_envelope,
    build_disruption_refreshed_payload,
)
from common.events.typed_events import DisruptionRefreshedEvent


def test_build_disruption_refreshed_payload_key_set_matches_model() -> None:
    payload = build_disruption_refreshed_payload(
        role_count=0,
        displacement_count=0,
        augmentation_count=0,
        transformation_count=0,
        emergence_count=0,
        refresh_duration_ms=0,
    )
    assert frozenset(payload.keys()) == frozenset(DisruptionRefreshedPayload.model_fields.keys())


def test_build_payload_and_envelope_shape() -> None:
    payload = build_disruption_refreshed_payload(
        role_count=3,
        displacement_count=1,
        augmentation_count=2,
        transformation_count=1,
        emergence_count=0,
        refresh_duration_ms=42,
    )
    assert payload["event_type"] == "DisruptionRefreshed"
    assert payload["role_count"] == 3
    assert payload["displacement_count"] == 1
    assert payload["augmentation_count"] == 2
    assert payload["transformation_count"] == 1
    assert payload["emergence_count"] == 0
    assert payload["refresh_duration_ms"] == 42
    assert payload["schema_version"] == "1.0"

    env = build_disruption_refreshed_envelope(
        correlation_id="corr-1",
        role_count=3,
        displacement_count=1,
        augmentation_count=2,
        transformation_count=1,
        emergence_count=0,
        refresh_duration_ms=42,
    )
    assert env.correlation_id == "corr-1"
    assert env.agent_id == "analytics-agent"
    assert env.payload == payload


def test_payload_coerces_negative_to_zero() -> None:
    p = DisruptionRefreshedPayload(
        role_count=-1,
        displacement_count=0,
        augmentation_count=0,
        transformation_count=0,
        emergence_count=0,
        refresh_duration_ms=-5,
    )
    assert p.role_count == 0
    assert p.refresh_duration_ms == 0


def test_disruption_refreshed_event_validator() -> None:
    env = build_disruption_refreshed_envelope(
        correlation_id="c",
        role_count=0,
        displacement_count=0,
        augmentation_count=0,
        transformation_count=0,
        emergence_count=0,
        refresh_duration_ms=0,
    )
    typed = DisruptionRefreshedEvent(envelope=env)
    assert typed.correlation_id == "c"


def test_disruption_refreshed_event_rejects_wrong_type() -> None:
    from common.event_envelope import EventEnvelope

    bad = EventEnvelope(
        correlation_id="c",
        agent_id="analytics-agent",
        payload={"event_type": "Other"},
    )
    with pytest.raises(ValueError, match="DisruptionRefreshed"):
        DisruptionRefreshedEvent(envelope=bad)
