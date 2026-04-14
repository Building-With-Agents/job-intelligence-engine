"""Disruption fingerprint computation (scaffold; issues #104–#108)."""

from __future__ import annotations

from analytics.disruption.classifier import DisruptionClassifier
from analytics.disruption.models import (
    DisruptionFingerprintRecord,
    DisruptionFingerprintResult,
    DisruptionRefreshResult,
    RoleDisruptionMetrics,
    TEMPORAL_PERIOD_SEQUENCE,
    TemporalPeriodComparison,
    TemporalPeriodSnapshot,
    build_period_comparison,
    build_fingerprint_hash_material,
    normalize_temporal_snapshots,
)
from analytics.disruption.repository import DisruptionFingerprintRepository
from analytics.disruption.service import DisruptionFingerprintService

__all__ = [
    "DisruptionClassifier",
    "DisruptionFingerprintRecord",
    "DisruptionFingerprintRepository",
    "DisruptionFingerprintResult",
    "DisruptionFingerprintService",
    "DisruptionRefreshResult",
    "RoleDisruptionMetrics",
    "TEMPORAL_PERIOD_SEQUENCE",
    "TemporalPeriodComparison",
    "TemporalPeriodSnapshot",
    "build_period_comparison",
    "build_fingerprint_hash_material",
    "normalize_temporal_snapshots",
]
