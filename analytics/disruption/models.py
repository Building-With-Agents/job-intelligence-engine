"""Typed models for disruption fingerprinting (scaffold — see issues #104–#108)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TemporalPeriodSnapshot(BaseModel):
    """Demand or volume signal for one temporal bucket on a canonical role.

    ``temporal_period`` aligns with enrichment-locked literals (e.g. ``post_gpt4``);
    full wiring lives in later issues.
    """

    model_config = ConfigDict(frozen=True)

    temporal_period: str
    posting_count: int = 0


class RoleDisruptionMetrics(BaseModel):
    """Aggregated inputs used to classify disruption for one canonical role."""

    model_config = ConfigDict(frozen=True)

    canonical_role_id: str
    snapshots: tuple[TemporalPeriodSnapshot, ...] = Field(default_factory=tuple)
    # TODO(#105): attach velocity, co-occurrence, skill-mix signals from aggregates.


def build_fingerprint_hash_material(
    role_id: str,
    snapshots: Sequence[TemporalPeriodSnapshot],
    categories: Sequence[str],
) -> dict[str, Any]:
    """Build JSON-serializable material for a stable content hash (#104).

    Snapshots are ordered by ``(temporal_period, posting_count)``; category strings
    are sorted lexicographically for determinism.
    """
    ordered = sorted(snapshots, key=lambda s: (s.temporal_period, s.posting_count))
    return {
        "canonical_role_id": role_id,
        "snapshots": [{"temporal_period": s.temporal_period, "posting_count": s.posting_count} for s in ordered],
        "disruption_categories": sorted({str(c) for c in categories}),
    }


class DisruptionFingerprintRecord(BaseModel):
    """One row-shaped fingerprint aligned with ``dbo.disruption_fingerprints`` (scaffold).

    Scalar / JSONB columns use safe Phase-1 defaults until #105–#106 populate signals.
    ``content_fingerprint`` is an in-memory SHA-256 over :func:`build_fingerprint_hash_material`
    for temporal diffing (#104); persistence mapping is #108.
    """

    model_config = ConfigDict(frozen=True)

    canonical_role_id: str
    disruption_category: list[str] = Field(default_factory=list)
    disruption_intensity: float = 0.0
    skill_velocity: list[dict[str, Any]] = Field(default_factory=list)
    tool_transition: list[dict[str, Any]] = Field(default_factory=list)
    task_shift: list[dict[str, Any]] = Field(default_factory=list)
    responsibility_expansion: float = 0.0
    ai_intensity_trend: str = "stable"
    workflow_restructuring_score: float = 0.0
    trajectory: str = "stable"
    period_comparison: list[dict[str, Any]] = Field(default_factory=list)
    content_fingerprint: str = ""


# Backward-compatible name for the record type (issue #103 naming drift).
DisruptionFingerprintResult = DisruptionFingerprintRecord


class DisruptionRefreshResult(BaseModel):
    """Outcome of a single :meth:`~analytics.disruption.service.DisruptionFingerprintService.refresh_disruption_fingerprints` run."""

    model_config = ConfigDict(frozen=True)

    fingerprints: list[DisruptionFingerprintRecord] = Field(default_factory=list)
    roles_considered: int = 0
    computed_count: int = 0
