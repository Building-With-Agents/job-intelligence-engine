"""Optional DB round-trip for ``save_fingerprints`` (tagged row + teardown only).

Requires ``PYTHON_DATABASE_URL``, reachable DB, and ``dbo.disruption_fingerprints``.
Skips when any prerequisite is missing (CI without DB).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, inspect

from analytics.disruption.models import DisruptionFingerprintRecord
from analytics.disruption.repository import DisruptionFingerprintRepository
from common.data_store.database import check_db_connection, get_engine, session_scope
from common.data_store.models import DisruptionFingerprint

pytestmark = pytest.mark.skipif(
    not os.getenv("PYTHON_DATABASE_URL"),
    reason="PYTHON_DATABASE_URL not set — disruption_fingerprints round-trip skipped",
)


def test_save_fingerprints_merges_tagged_row_roundtrip() -> None:
    if not check_db_connection():
        pytest.skip("database unreachable")
    insp = inspect(get_engine())
    if not insp.has_table("disruption_fingerprints", schema="dbo"):
        pytest.skip("dbo.disruption_fingerprints missing — run migrations")

    rid = f"test-dfp4-{uuid.uuid4().hex[:16]}"
    skill_v = [{"skill_name": "Python", "velocity": 0.15}]
    period_c = [{"metric": "postings", "current": 2, "previous": 1, "delta_pct": 1.0}]
    rec = DisruptionFingerprintRecord(
        canonical_role_id=rid,
        disruption_category=["Augmentation"],
        skill_velocity=skill_v,
        tool_transition=[],
        task_shift=[],
        period_comparison=period_c,
        content_fingerprint="deadbeef" * 8,
        disruption_intensity=0.55,
        trajectory="stable",
        ai_intensity_trend="increasing",
    )
    try:
        with session_scope() as session:
            DisruptionFingerprintRepository().save_fingerprints([rec], session)

        with session_scope() as session:
            row = session.get(DisruptionFingerprint, rid)
        assert row is not None
        assert row.disruption_category == ["Augmentation"]
        assert row.skill_velocity == skill_v
        assert row.period_comparison == period_c
        assert row.content_fingerprint == rec.content_fingerprint
        assert row.disruption_intensity == 0.55
        assert row.trajectory == "stable"
        assert row.ai_intensity_trend == "increasing"
        assert row.computed_at is not None
    finally:
        with session_scope() as session:
            session.execute(delete(DisruptionFingerprint).where(DisruptionFingerprint.canonical_role_id == rid))
