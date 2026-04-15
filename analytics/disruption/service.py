"""Orchestrate disruption fingerprint refresh (scaffold)."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from statistics import fmean
from typing import TYPE_CHECKING, Any

import structlog

from analytics.disruption.classifier import DisruptionClassifier
from analytics.disruption.models import (
    DisruptionFingerprintRecord,
    DisruptionRefreshResult,
    RoleDisruptionMetrics,
    TemporalPeriodComparison,
    TemporalPeriodSnapshot,
    build_fingerprint_hash_material,
    build_period_comparison,
    normalize_temporal_snapshots,
)
from analytics.disruption.repository import DisruptionFingerprintRepository
from common.events.disruption_refreshed import build_disruption_refreshed_envelope

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger()

_DISRUPTION_CATEGORY_LABELS: tuple[str, str, str, str] = (
    "Displacement",
    "Augmentation",
    "Transformation",
    "Emergence",
)

_disruption_refreshed_bus: Any | None = None


def register_disruption_refreshed_bus(bus: Any | None) -> None:
    """Register the in-process bus so ``DisruptionRefreshed`` can be published after refresh."""
    global _disruption_refreshed_bus
    _disruption_refreshed_bus = bus


def _fingerprints_to_category_counts(
    fingerprints: list[DisruptionFingerprintRecord],
) -> tuple[int, int, int, int, int]:
    """Return ``(role_count, displacement, augmentation, transformation, emergence)``.

    Each count is the number of fingerprint rows whose ``disruption_category`` includes
    that label (a row with multiple labels increments multiple buckets).
    """
    role_count = len(fingerprints)
    counts = {label: 0 for label in _DISRUPTION_CATEGORY_LABELS}
    for fp in fingerprints:
        cats = set(fp.disruption_category)
        for label in _DISRUPTION_CATEGORY_LABELS:
            if label in cats:
                counts[label] += 1
    return (
        role_count,
        counts["Displacement"],
        counts["Augmentation"],
        counts["Transformation"],
        counts["Emergence"],
    )
_TREND_EPSILON = 0.05
_TOP_SKILL_VELOCITY = 5


def _content_fingerprint_hex(role_id: str, snapshots: tuple[TemporalPeriodSnapshot, ...], categories: list[str]) -> str:
    """SHA-256 hex digest over canonical hash material."""
    material = build_fingerprint_hash_material(role_id, snapshots, categories)
    raw = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class DisruptionFingerprintService:
    """Run one in-memory disruption fingerprint pass over canonical roles."""

    def __init__(
        self,
        repository: DisruptionFingerprintRepository | None = None,
        classifier: DisruptionClassifier | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._repository = repository or DisruptionFingerprintRepository()
        self._classifier = classifier or DisruptionClassifier()
        self._event_bus = event_bus

    def refresh_disruption_fingerprints(
        self,
        session: Session | None = None,
        correlation_id: str | None = None,
    ) -> DisruptionRefreshResult:
        """Fetch roles, snapshots per role, build metrics, classify, hash, persist, return aggregate result.

        Args:
            session: Optional SQLAlchemy session for repository I/O.
            correlation_id: Optional pipeline correlation id for ``DisruptionRefreshed``; if unset, a UUID is used.

        Returns:
            :class:`DisruptionRefreshResult` with fingerprints and counts (safe on empty roles).
        """
        t0 = time.perf_counter()
        role_ids = self._repository.fetch_canonical_roles(session)
        fingerprints: list[DisruptionFingerprintRecord] = []

        for role_id in role_ids:
            snapshots = self._repository.fetch_period_snapshots(role_id, session)
            metrics = _build_placeholder_metrics(role_id, snapshots)
            categories = self._classifier.classify(metrics)
            cat_list = list(categories)
            snap_tuple = metrics.snapshots
            skill_velocity = _compute_skill_velocity(metrics.snapshots)
            tool_transition = _compute_tool_transition(metrics.snapshots)
            task_shift = _compute_task_shift(metrics.snapshots)
            responsibility_expansion = _compute_responsibility_expansion(metrics.snapshots)
            ai_intensity_trend = _compute_ai_intensity_trend(metrics.snapshots)
            content_fp = _content_fingerprint_hex(role_id, snap_tuple, cat_list)
            fingerprints.append(
                DisruptionFingerprintRecord(
                    canonical_role_id=role_id,
                    disruption_category=list(cat_list),
                    skill_velocity=skill_velocity,
                    tool_transition=tool_transition,
                    task_shift=task_shift,
                    responsibility_expansion=responsibility_expansion,
                    ai_intensity_trend=ai_intensity_trend,
                    workflow_restructuring_score=_compute_workflow_restructuring_score(
                        metrics.period_comparison,
                        tool_transition,
                        task_shift,
                        responsibility_expansion,
                    ),
                    period_comparison=[cmp.model_dump() for cmp in metrics.period_comparison],
                    content_fingerprint=content_fp,
                )
            )

        self._repository.save_fingerprints(fingerprints, session)

        duration_ms = int(max(0.0, (time.perf_counter() - t0) * 1000.0))

        result = DisruptionRefreshResult(
            fingerprints=fingerprints,
            roles_considered=len(role_ids),
            computed_count=len(fingerprints),
        )

        self._emit_disruption_refreshed(fingerprints, correlation_id, duration_ms)

        log.info(
            "disruption_fingerprints_refresh",
            roles_considered=result.roles_considered,
            computed_count=result.computed_count,
            refresh_duration_ms=duration_ms,
        )
        return result

    def _emit_disruption_refreshed(
        self,
        fingerprints: list[DisruptionFingerprintRecord],
        correlation_id: str | None,
        duration_ms: int,
    ) -> None:
        bus = self._event_bus if self._event_bus is not None else _disruption_refreshed_bus
        if bus is None:
            return
        cid = (correlation_id or "").strip() or str(uuid.uuid4())
        rc, d_ct, a_ct, t_ct, e_ct = _fingerprints_to_category_counts(fingerprints)
        envelope = build_disruption_refreshed_envelope(
            correlation_id=cid,
            role_count=rc,
            displacement_count=d_ct,
            augmentation_count=a_ct,
            transformation_count=t_ct,
            emergence_count=e_ct,
            refresh_duration_ms=max(0, duration_ms),
        )
        try:
            bus.publish(envelope)
        except Exception as exc:
            log.warning(
                "disruption_refreshed_publish_failed",
                error=str(exc),
                correlation_id=cid,
            )


def _build_placeholder_metrics(
    role_id: str,
    snapshots: list[TemporalPeriodSnapshot],
) -> RoleDisruptionMetrics:
    """Assemble metrics from repository snapshots with period normalization."""
    normalized_snapshots = normalize_temporal_snapshots(snapshots)
    period_comparison = build_period_comparison(normalized_snapshots)
    return RoleDisruptionMetrics(
        canonical_role_id=role_id,
        snapshots=normalized_snapshots,
        period_comparison=period_comparison,
    )


def _compute_skill_velocity(
    snapshots: tuple[TemporalPeriodSnapshot, ...],
    top_k: int = _TOP_SKILL_VELOCITY,
) -> list[dict[str, Any]]:
    """Return top changing skills across ordered temporal periods."""
    if len(snapshots) < 2:
        return []

    net_change: dict[str, float] = defaultdict(float)
    max_step_delta: dict[str, float] = defaultdict(float)
    changed_steps: dict[str, int] = defaultdict(int)

    for left, right in zip(snapshots, snapshots[1:]):
        keys = set(left.skill_mix) | set(right.skill_mix)
        for skill in keys:
            delta = right.skill_mix.get(skill, 0.0) - left.skill_mix.get(skill, 0.0)
            net_change[skill] += delta
            if abs(delta) > abs(max_step_delta[skill]):
                max_step_delta[skill] = delta
            if delta != 0:
                changed_steps[skill] += 1

    ranked = sorted(
        net_change,
        key=lambda s: (abs(net_change[s]), abs(max_step_delta[s]), s),
        reverse=True,
    )[:top_k]

    out: list[dict[str, Any]] = []
    for skill in ranked:
        net = net_change[skill]
        direction = "stable"
        if net > 0:
            direction = "increasing"
        elif net < 0:
            direction = "decreasing"
        out.append(
            {
                "skill_name": skill,
                "direction": direction,
                "net_change": round(net, 6),
                "max_step_delta": round(max_step_delta[skill], 6),
                "changed_steps": changed_steps.get(skill, 0),
            }
        )
    return out


def _tool_transition_for_pair(left: TemporalPeriodSnapshot, right: TemporalPeriodSnapshot) -> dict[str, Any]:
    left_tools = set(left.tool_mix)
    right_tools = set(right.tool_mix)
    adopted = sorted(right_tools - left_tools)
    abandoned = sorted(left_tools - right_tools)
    union_count = len(left_tools | right_tools)
    changed_count = len(adopted) + len(abandoned)
    magnitude = (changed_count / union_count) if union_count else 0.0
    return {
        "from_period": left.temporal_period,
        "to_period": right.temporal_period,
        "adopted_tools": adopted,
        "abandoned_tools": abandoned,
        "net_tool_change": len(adopted) - len(abandoned),
        "transition_magnitude": round(magnitude, 6),
        "from_missing": not left.has_observed_data,
        "to_missing": not right.has_observed_data,
    }


def _compute_tool_transition(snapshots: tuple[TemporalPeriodSnapshot, ...]) -> list[dict[str, Any]]:
    if len(snapshots) < 2:
        return []
    transitions = [_tool_transition_for_pair(left, right) for left, right in zip(snapshots, snapshots[1:])]
    return [t for t in transitions if t["adopted_tools"] or t["abandoned_tools"]]


def _task_shift_for_pair(left: TemporalPeriodSnapshot, right: TemporalPeriodSnapshot) -> dict[str, Any]:
    left_tasks = set(left.task_mix)
    right_tasks = set(right.task_mix)
    union = left_tasks | right_tasks
    retained = left_tasks & right_tasks
    jaccard = (len(retained) / len(union)) if union else 1.0
    return {
        "from_period": left.temporal_period,
        "to_period": right.temporal_period,
        "added_tasks": sorted(right_tasks - left_tasks),
        "removed_tasks": sorted(left_tasks - right_tasks),
        "retained_task_count": len(retained),
        "task_jaccard_similarity": round(jaccard, 6),
        "task_change_ratio": round(1.0 - jaccard, 6),
        "from_missing": not left.has_observed_data,
        "to_missing": not right.has_observed_data,
    }


def _compute_task_shift(snapshots: tuple[TemporalPeriodSnapshot, ...]) -> list[dict[str, Any]]:
    if len(snapshots) < 2:
        return []
    return [_task_shift_for_pair(left, right) for left, right in zip(snapshots, snapshots[1:])]


def _observed_snapshots(snapshots: tuple[TemporalPeriodSnapshot, ...]) -> tuple[TemporalPeriodSnapshot, ...]:
    return tuple(s for s in snapshots if s.has_observed_data)


def _compute_responsibility_expansion(snapshots: tuple[TemporalPeriodSnapshot, ...]) -> float:
    observed = _observed_snapshots(snapshots)
    if len(observed) < 2:
        return 0.0
    start = observed[0].responsibility_density
    end = observed[-1].responsibility_density
    return round(end - start, 6)


def _compute_ai_intensity_trend(snapshots: tuple[TemporalPeriodSnapshot, ...]) -> str:
    observed = _observed_snapshots(snapshots)
    if len(observed) < 2:
        return "stable"
    delta = observed[-1].ai_requirement_density - observed[0].ai_requirement_density
    if delta > _TREND_EPSILON:
        return "increasing"
    if delta < (-1 * _TREND_EPSILON):
        return "decreasing"
    return "stable"


def _compute_workflow_restructuring_score(
    period_comparison: tuple[TemporalPeriodComparison, ...],
    tool_transition: list[dict[str, Any]],
    task_shift: list[dict[str, Any]],
    responsibility_expansion: float,
) -> float:
    skill_change_component = fmean([cmp.skill_composition_change_ratio for cmp in period_comparison]) if period_comparison else 0.0
    tool_change_component = fmean([t["transition_magnitude"] for t in tool_transition]) if tool_transition else 0.0
    task_change_component = fmean([t["task_change_ratio"] for t in task_shift]) if task_shift else 0.0
    responsibility_component = min(1.0, abs(responsibility_expansion))

    raw_score = (
        (skill_change_component * 0.35)
        + (tool_change_component * 0.25)
        + (task_change_component * 0.25)
        + (responsibility_component * 0.15)
    )
    return round(max(0.0, min(1.0, raw_score)), 6)
