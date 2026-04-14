"""Persistence and read paths for disruption fingerprints (stubs until #108)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from analytics.disruption.models import DisruptionFingerprintRecord, TemporalPeriodSnapshot

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class DisruptionFingerprintRepository:
    """Load canonical roles and period snapshots; persist fingerprint rows.

    Default methods are no-op / empty returns. Replace with SQLAlchemy queries
    when ``dbo.disruption_fingerprints`` and aggregate reads are wired (#108).
    """

    def fetch_canonical_roles(self, session: Session | None = None) -> list[str]:
        """Return canonical ``role_id`` values to analyze.

        TODO(#108): ``SELECT role_id FROM dbo.canonical_roles`` (or agreed source).
        """
        return []

    def fetch_period_snapshots(
        self,
        role_id: str,
        session: Session | None = None,
    ) -> list[TemporalPeriodSnapshot]:
        """Return temporal-period slices for ``role_id``.

        TODO(#104): compare windows; TODO(#105): real metrics from ``job_postings`` rollups.
        """
        return []

    def save_fingerprints(
        self,
        results: list[DisruptionFingerprintRecord],
        session: Session | None = None,
    ) -> None:
        """Persist fingerprint results.

        TODO(#108): upsert into ``dbo.disruption_fingerprints`` + optional event emission.
        """
        _ = session
        _ = results
