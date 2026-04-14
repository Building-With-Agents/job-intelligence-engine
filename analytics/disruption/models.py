"""Typed models for disruption fingerprinting (scaffold — see issues #104–#108)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TEMPORAL_PERIOD_SEQUENCE: tuple[str, ...] = (
    "pre_chatgpt",
    "early_genai",
    "post_gpt4",
    "agentic_era",
)


class TemporalPeriodSnapshot(BaseModel):
    """Demand or volume signal for one temporal bucket on a canonical role.

    ``temporal_period`` aligns with enrichment-locked literals (e.g. ``post_gpt4``);
    full wiring lives in later issues.
    """

    model_config = ConfigDict(frozen=True)

    temporal_period: str
    posting_count: int = 0
    skill_mix: dict[str, float] = Field(default_factory=dict)
    has_observed_data: bool = True


class TemporalPeriodComparison(BaseModel):
    """Comparison summary between two adjacent temporal periods."""

    model_config = ConfigDict(frozen=True)

    from_period: str
    to_period: str
    from_missing: bool = False
    to_missing: bool = False
    posting_count_from: int = 0
    posting_count_to: int = 0
    posting_count_delta: int = 0
    posting_count_change_ratio: float | None = None
    from_skill_count: int = 0
    to_skill_count: int = 0
    retained_skill_count: int = 0
    added_skill_count: int = 0
    removed_skill_count: int = 0
    skill_jaccard_similarity: float = 0.0
    skill_composition_change_ratio: float = 0.0


class RoleDisruptionMetrics(BaseModel):
    """Aggregated inputs used to classify disruption for one canonical role."""

    model_config = ConfigDict(frozen=True)

    canonical_role_id: str
    snapshots: tuple[TemporalPeriodSnapshot, ...] = Field(default_factory=tuple)
    period_comparison: tuple[TemporalPeriodComparison, ...] = Field(default_factory=tuple)
    # TODO(#105): attach velocity, co-occurrence, additional skill-mix signals from aggregates.


def normalize_temporal_snapshots(
    snapshots: Sequence[TemporalPeriodSnapshot],
) -> tuple[TemporalPeriodSnapshot, ...]:
    """Return snapshots normalized to the locked 4-period order.

    Missing periods are represented with zeroed placeholder snapshots and
    ``has_observed_data=False`` so callers can reason about sparse inputs.
    """
    by_period = {s.temporal_period: s for s in snapshots if s.temporal_period in TEMPORAL_PERIOD_SEQUENCE}
    normalized: list[TemporalPeriodSnapshot] = []
    for period in TEMPORAL_PERIOD_SEQUENCE:
        existing = by_period.get(period)
        if existing is not None:
            normalized.append(existing)
            continue
        normalized.append(
            TemporalPeriodSnapshot(
                temporal_period=period,
                posting_count=0,
                skill_mix={},
                has_observed_data=False,
            )
        )
    return tuple(normalized)


def build_period_comparison(
    snapshots: Sequence[TemporalPeriodSnapshot],
) -> tuple[TemporalPeriodComparison, ...]:
    """Build adjacent period comparisons across the 4 temporal buckets."""
    ordered = normalize_temporal_snapshots(snapshots)
    out: list[TemporalPeriodComparison] = []
    for left, right in zip(ordered, ordered[1:]):
        left_skills = set(left.skill_mix.keys())
        right_skills = set(right.skill_mix.keys())
        union = left_skills | right_skills
        intersection = left_skills & right_skills
        jaccard = (len(intersection) / len(union)) if union else 1.0
        delta = right.posting_count - left.posting_count
        change_ratio = None if left.posting_count <= 0 else (delta / left.posting_count)
        out.append(
            TemporalPeriodComparison(
                from_period=left.temporal_period,
                to_period=right.temporal_period,
                from_missing=not left.has_observed_data,
                to_missing=not right.has_observed_data,
                posting_count_from=left.posting_count,
                posting_count_to=right.posting_count,
                posting_count_delta=delta,
                posting_count_change_ratio=change_ratio,
                from_skill_count=len(left_skills),
                to_skill_count=len(right_skills),
                retained_skill_count=len(intersection),
                added_skill_count=len(right_skills - left_skills),
                removed_skill_count=len(left_skills - right_skills),
                skill_jaccard_similarity=jaccard,
                skill_composition_change_ratio=1.0 - jaccard,
            )
        )
    return tuple(out)


def build_fingerprint_hash_material(
    role_id: str,
    snapshots: Sequence[TemporalPeriodSnapshot],
    categories: Sequence[str],
) -> dict[str, Any]:
    """Build JSON-serializable material for a stable content hash (#104).

    Snapshots are normalized to the locked temporal sequence and category strings
    are sorted lexicographically for determinism.
    """
    ordered = normalize_temporal_snapshots(snapshots)
    return {
        "canonical_role_id": role_id,
        "snapshots": [
            {
                "temporal_period": s.temporal_period,
                "posting_count": s.posting_count,
                "skill_mix": {k: s.skill_mix[k] for k in sorted(s.skill_mix)},
                "has_observed_data": s.has_observed_data,
            }
            for s in ordered
        ],
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
