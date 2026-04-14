"""Tests for disruption fingerprint service scaffold."""

from __future__ import annotations

from analytics.disruption import (
    DisruptionFingerprintRecord,
    DisruptionFingerprintRepository,
    DisruptionFingerprintService,
    TemporalPeriodSnapshot,
    build_fingerprint_hash_material,
)


class _FakeRepository(DisruptionFingerprintRepository):
    """In-memory repository for unit tests (no database)."""

    def __init__(self) -> None:
        self.saved: list[DisruptionFingerprintRecord] | None = None

    def fetch_canonical_roles(self, session=None) -> list[str]:
        return ["role-a", "role-b"]

    def fetch_period_snapshots(self, role_id: str, session=None) -> list[TemporalPeriodSnapshot]:
        return [
            TemporalPeriodSnapshot(temporal_period="post_gpt4", posting_count=3 if role_id == "role-a" else 1),
        ]

    def save_fingerprints(self, results: list[DisruptionFingerprintRecord], session=None) -> None:
        self.saved = list(results)


class _EmptyRepository(DisruptionFingerprintRepository):
    """Repository that yields no roles."""

    def __init__(self) -> None:
        self.saved: list[DisruptionFingerprintRecord] | None = None

    def save_fingerprints(self, results: list[DisruptionFingerprintRecord], session=None) -> None:
        self.saved = list(results)


def test_refresh_empty_repository_returns_zero_counts_and_no_crash() -> None:
    repo = _EmptyRepository()
    svc = DisruptionFingerprintService(repository=repo)
    out = svc.refresh_disruption_fingerprints(session=None)
    assert out.fingerprints == []
    assert out.roles_considered == 0
    assert out.computed_count == 0
    assert repo.saved is not None
    assert repo.saved == []


def test_fingerprint_hash_material_is_deterministic_for_logically_identical_input() -> None:
    """Snapshot order in input must not change the hashed material (sorted in helper)."""
    snaps_a = [
        TemporalPeriodSnapshot(temporal_period="early_genai", posting_count=2),
        TemporalPeriodSnapshot(temporal_period="post_gpt4", posting_count=5),
    ]
    snaps_b = list(reversed(snaps_a))
    cats = ["Emergence", "Displacement", "Emergence"]  # duplicates + order should not matter
    m1 = build_fingerprint_hash_material("role-x", snaps_a, cats)
    m2 = build_fingerprint_hash_material("role-x", snaps_b, reversed(cats))
    assert m1 == m2
    assert m1["disruption_categories"] == ["Displacement", "Emergence"]


def test_refresh_disruption_fingerprints_fake_repository_happy_path() -> None:
    repo = _FakeRepository()
    svc = DisruptionFingerprintService(repository=repo)
    out = svc.refresh_disruption_fingerprints(session=None)
    assert out.roles_considered == 2
    assert out.computed_count == 2
    assert len(out.fingerprints) == 2
    assert {r.canonical_role_id for r in out.fingerprints} == {"role-a", "role-b"}
    assert all(isinstance(r.content_fingerprint, str) and len(r.content_fingerprint) == 64 for r in out.fingerprints)
    assert repo.saved is not None
    assert len(repo.saved) == 2


def test_hash_determinism_end_to_end_same_snapshots_same_fingerprint() -> None:
    """Two service runs with identical repo data yield identical content_fingerprint per role."""

    class _DeterministicRepo(DisruptionFingerprintRepository):
        def save_fingerprints(self, results, session=None) -> None:
            pass

        def fetch_canonical_roles(self, session=None) -> list[str]:
            return ["same-role"]

        def fetch_period_snapshots(self, role_id: str, session=None) -> list[TemporalPeriodSnapshot]:
            return [
                TemporalPeriodSnapshot(temporal_period="post_gpt4", posting_count=7),
                TemporalPeriodSnapshot(temporal_period="early_genai", posting_count=1),
            ]

    r1 = _DeterministicRepo()
    r2 = _DeterministicRepo()
    fp1 = DisruptionFingerprintService(repository=r1).refresh_disruption_fingerprints().fingerprints[0].content_fingerprint
    fp2 = DisruptionFingerprintService(repository=r2).refresh_disruption_fingerprints().fingerprints[0].content_fingerprint
    assert fp1 == fp2
