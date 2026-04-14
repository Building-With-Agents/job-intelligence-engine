"""Map role-level metrics to disruption pattern labels (stub until #106)."""

from __future__ import annotations

from analytics.disruption.models import RoleDisruptionMetrics


class DisruptionClassifier:
    """Classify ``RoleDisruptionMetrics`` into disruption category strings."""

    def classify(self, metrics: RoleDisruptionMetrics) -> list[str]:
        """Return disruption pattern labels for ``metrics``.

        Phase-1 scaffold: no rules yet. Later: Displacement | Augmentation |
        Transformation | Emergence per ARCHITECTURE_DEEP (#106).

        Args:
            metrics: Aggregated signals for one canonical role.

        Returns:
            Category labels; empty until classification logic exists.
        """
        _ = metrics
        return []
