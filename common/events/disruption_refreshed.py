"""DisruptionRefreshed payload builder (Analytics disruption fingerprint step, issue #108).

Published after ``DisruptionFingerprintService.refresh_disruption_fingerprints`` completes.
Downstream consumers use the same :class:`common.event_envelope.EventEnvelope` bus shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from common.event_envelope import EventEnvelope

_ANALYTICS_AGENT_ID = "analytics-agent"


class DisruptionRefreshedPayload(BaseModel):
    """Validated payload for ``DisruptionRefreshed``."""

    model_config = {"extra": "forbid"}

    event_type: str = Field(default="DisruptionRefreshed")
    role_count: int = Field(ge=0)
    displacement_count: int = Field(ge=0)
    augmentation_count: int = Field(ge=0)
    transformation_count: int = Field(ge=0)
    emergence_count: int = Field(ge=0)
    refresh_duration_ms: int = Field(ge=0)

    schema_version: str = Field(default="1.0")

    @field_validator(
        "role_count",
        "displacement_count",
        "augmentation_count",
        "transformation_count",
        "emergence_count",
        "refresh_duration_ms",
        mode="before",
    )
    @classmethod
    def _coerce_non_negative_int(cls, v: Any) -> int:
        try:
            n = int(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected int-coercible value, got {v!r}") from exc
        return max(0, n)


def build_disruption_refreshed_payload(
    *,
    role_count: int,
    displacement_count: int,
    augmentation_count: int,
    transformation_count: int,
    emergence_count: int,
    refresh_duration_ms: int,
) -> dict[str, Any]:
    """Return payload dict suitable for :class:`EventEnvelope`."""
    return DisruptionRefreshedPayload(
        role_count=role_count,
        displacement_count=displacement_count,
        augmentation_count=augmentation_count,
        transformation_count=transformation_count,
        emergence_count=emergence_count,
        refresh_duration_ms=refresh_duration_ms,
    ).model_dump()


def build_disruption_refreshed_envelope(
    *,
    correlation_id: str,
    role_count: int,
    displacement_count: int,
    augmentation_count: int,
    transformation_count: int,
    emergence_count: int,
    refresh_duration_ms: int,
) -> EventEnvelope:
    """Build the canonical ``DisruptionRefreshed`` envelope (``analytics-agent`` producer)."""
    return EventEnvelope(
        correlation_id=correlation_id,
        agent_id=_ANALYTICS_AGENT_ID,
        schema_version="1.0",
        payload=build_disruption_refreshed_payload(
            role_count=role_count,
            displacement_count=displacement_count,
            augmentation_count=augmentation_count,
            transformation_count=transformation_count,
            emergence_count=emergence_count,
            refresh_duration_ms=refresh_duration_ms,
        ),
    )
