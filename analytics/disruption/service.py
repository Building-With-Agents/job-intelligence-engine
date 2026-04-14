"""Orchestrate disruption fingerprint refresh (scaffold)."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import structlog

from analytics.disruption.classifier import DisruptionClassifier
from analytics.disruption.models import (
    DisruptionFingerprintRecord,
    DisruptionRefreshResult,
    RoleDisruptionMetrics,
    TemporalPeriodSnapshot,
    build_fingerprint_hash_material,
)
from analytics.disruption.repository import DisruptionFingerprintRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger()


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
    ) -> None:
        self._repository = repository or DisruptionFingerprintRepository()
        self._classifier = classifier or DisruptionClassifier()

    def refresh_disruption_fingerprints(self, session: Session | None = None) -> DisruptionRefreshResult:
        """Fetch roles, snapshots per role, build metrics, classify, hash, persist (stub), return aggregate result.

        Args:
            session: Optional SQLAlchemy session for future repository I/O (#108).

        Returns:
            :class:`DisruptionRefreshResult` with fingerprints and counts (safe on empty roles).
        """
        role_ids = self._repository.fetch_canonical_roles(session)
        fingerprints: list[DisruptionFingerprintRecord] = []

        for role_id in role_ids:
            snapshots = self._repository.fetch_period_snapshots(role_id, session)
            metrics = _build_placeholder_metrics(role_id, snapshots)
            categories = self._classifier.classify(metrics)
            cat_list = list(categories)
            snap_tuple = metrics.snapshots
            content_fp = _content_fingerprint_hex(role_id, snap_tuple, cat_list)
            fingerprints.append(
                DisruptionFingerprintRecord(
                    canonical_role_id=role_id,
                    disruption_category=list(cat_list),
                    content_fingerprint=content_fp,
                )
            )

        self._repository.save_fingerprints(fingerprints, session)

        result = DisruptionRefreshResult(
            fingerprints=fingerprints,
            roles_considered=len(role_ids),
            computed_count=len(fingerprints),
        )

        log.info(
            "disruption_fingerprints_refresh",
            roles_considered=result.roles_considered,
            computed_count=result.computed_count,
        )
        return result


def _build_placeholder_metrics(
    role_id: str,
    snapshots: list[TemporalPeriodSnapshot],
) -> RoleDisruptionMetrics:
    """Assemble metrics from repository snapshots (extend in #105)."""
    if not snapshots:
        # TODO(#104): default windows / empty-window semantics when snapshots missing.
        return RoleDisruptionMetrics(canonical_role_id=role_id, snapshots=tuple())
    return RoleDisruptionMetrics(
        canonical_role_id=role_id,
        snapshots=tuple(snapshots),
    )
