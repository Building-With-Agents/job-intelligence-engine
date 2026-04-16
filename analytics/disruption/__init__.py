"""Disruption fingerprint computation (scaffold; issues #104–#108)."""

from __future__ import annotations

from analytics.disruption.classifier import DisruptionClassifier
from analytics.disruption.models import (
    TEMPORAL_PERIOD_SEQUENCE,
    DisruptionFingerprintRecord,
    DisruptionFingerprintResult,
    DisruptionRefreshResult,
    RoleDisruptionMetrics,
    TemporalPeriodComparison,
    TemporalPeriodSnapshot,
    build_fingerprint_hash_material,
    build_period_comparison,
    normalize_temporal_snapshots,
)
from analytics.disruption.repository import DisruptionFingerprintRepository
from analytics.disruption.service import (
    DisruptionFingerprintService,
    register_disruption_refreshed_bus,
)

__all__ = [
    "DisruptionClassifier",
    "DisruptionFingerprintRecord",
    "DisruptionFingerprintRepository",
    "DisruptionFingerprintResult",
    "DisruptionFingerprintService",
    "DisruptionRefreshResult",
    "register_disruption_refreshed_bus",
    "RoleDisruptionMetrics",
    "TEMPORAL_PERIOD_SEQUENCE",
    "TemporalPeriodComparison",
    "TemporalPeriodSnapshot",
    "build_period_comparison",
    "build_fingerprint_hash_material",
    "normalize_temporal_snapshots",
]
