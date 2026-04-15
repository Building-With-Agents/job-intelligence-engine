"""Tests for disruption fingerprint service (refresh, persist hook, DisruptionRefreshed)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from analytics.disruption import (
    TEMPORAL_PERIOD_SEQUENCE,
    DisruptionFingerprintRecord,
    DisruptionFingerprintRepository,
    DisruptionFingerprintService,
    TemporalPeriodSnapshot,
    build_fingerprint_hash_material,
    build_period_comparison,
    normalize_temporal_snapshots,
)
from analytics.disruption.classifier import DisruptionClassifier
from analytics.disruption.models import RoleDisruptionMetrics
from analytics.disruption.service import (
    _fingerprints_to_category_counts,
    register_disruption_refreshed_bus,
)
from common.events.disruption_refreshed import DisruptionRefreshedPayload

_DISRUPTION_REFRESHED_PAYLOAD_KEYS = frozenset(DisruptionRefreshedPayload.model_fields.keys())


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


# Snapshots aligned with tests/analytics/test_disruption_classifier.py (one pattern each).
_FOUR_PATTERN_ROLE_IDS: tuple[str, str, str, str] = (
    "role-verify-transformation",
    "role-verify-displacement",
    "role-verify-augmentation",
    "role-verify-emergence",
)

_FOUR_PATTERN_SNAPSHOTS: dict[str, list[TemporalPeriodSnapshot]] = {
    "role-verify-transformation": [
        TemporalPeriodSnapshot(
            temporal_period="early_genai",
            posting_count=100,
            skill_mix={"excel": 1.0, "powerpoint": 0.8},
        ),
        TemporalPeriodSnapshot(
            temporal_period="post_gpt4",
            posting_count=100,
            skill_mix={"python": 0.9, "sql": 0.7},
        ),
    ],
    "role-verify-displacement": [
        TemporalPeriodSnapshot(
            temporal_period="pre_chatgpt",
            posting_count=100,
            tool_mix={"excel": 1.0},
            responsibility_density=0.6,
            ai_requirement_density=0.05,
        ),
        TemporalPeriodSnapshot(
            temporal_period="agentic_era",
            posting_count=70,
            tool_mix={"excel": 0.8, "chatgpt": 0.7},
            responsibility_density=0.45,
            ai_requirement_density=0.2,
        ),
    ],
    "role-verify-augmentation": [
        TemporalPeriodSnapshot(
            temporal_period="pre_chatgpt",
            posting_count=100,
            tool_mix={"excel": 1.0},
            ai_requirement_density=0.05,
        ),
        TemporalPeriodSnapshot(
            temporal_period="agentic_era",
            posting_count=105,
            tool_mix={"excel": 0.8, "copilot": 0.5},
            ai_requirement_density=0.18,
        ),
    ],
    "role-verify-emergence": [
        TemporalPeriodSnapshot(
            temporal_period="agentic_era",
            posting_count=25,
            tool_mix={"langgraph": 0.7, "gpt-4": 0.9},
            ai_requirement_density=0.4,
        ),
    ],
}


class _FourPatternRepository(DisruptionFingerprintRepository):
    """Yields four canonical role ids with classifier-proven snapshots (issue #109)."""

    def __init__(self) -> None:
        self.saved: list[DisruptionFingerprintRecord] | None = None

    def fetch_canonical_roles(self, session=None) -> list[str]:
        return list(_FOUR_PATTERN_ROLE_IDS)

    def fetch_period_snapshots(self, role_id: str, session=None) -> list[TemporalPeriodSnapshot]:
        return list(_FOUR_PATTERN_SNAPSHOTS.get(role_id, []))

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


def test_normalize_temporal_snapshots_fills_missing_periods_with_defaults() -> None:
    snapshots = [
        TemporalPeriodSnapshot(temporal_period="post_gpt4", posting_count=5, skill_mix={"python": 0.4}),
    ]
    out = normalize_temporal_snapshots(snapshots)
    assert [s.temporal_period for s in out] == list(TEMPORAL_PERIOD_SEQUENCE)
    by_period = {s.temporal_period: s for s in out}
    assert by_period["post_gpt4"].has_observed_data is True
    assert by_period["post_gpt4"].posting_count == 5
    assert by_period["pre_chatgpt"].has_observed_data is False
    assert by_period["pre_chatgpt"].posting_count == 0
    assert by_period["pre_chatgpt"].skill_mix == {}


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
    assert all(len(r.period_comparison) == 3 for r in out.fingerprints)
    assert repo.saved is not None
    assert len(repo.saved) == 2


def test_refresh_covers_all_four_disruption_patterns_across_roles() -> None:
    """Issue #109: each pattern appears on ≥1 role; event counts match fingerprint rows."""
    bus = MagicMock()
    repo = _FourPatternRepository()
    svc = DisruptionFingerprintService(
        repository=repo,
        event_bus=bus,
        classifier=DisruptionClassifier(),
    )
    out = svc.refresh_disruption_fingerprints(session=None, correlation_id="corr-four-pattern")

    assert out.roles_considered == 4
    assert out.computed_count == 4
    assert len(out.fingerprints) == 4

    patterns = {"Displacement", "Augmentation", "Transformation", "Emergence"}
    union: set[str] = set()
    for fp in out.fingerprints:
        union |= set(fp.disruption_category)
        assert fp.canonical_role_id in _FOUR_PATTERN_SNAPSHOTS
    assert patterns <= union, f"missing patterns: {patterns - union}"

    role_to_cats = {fp.canonical_role_id: list(fp.disruption_category) for fp in out.fingerprints}
    assert "Transformation" in role_to_cats["role-verify-transformation"]
    assert "Displacement" in role_to_cats["role-verify-displacement"]
    assert "Augmentation" in role_to_cats["role-verify-augmentation"]
    assert "Emergence" in role_to_cats["role-verify-emergence"]

    rc, d_ct, a_ct, t_ct, e_ct = _fingerprints_to_category_counts(out.fingerprints)
    assert rc == 4
    assert d_ct >= 1 and a_ct >= 1 and t_ct >= 1 and e_ct >= 1

    bus.publish.assert_called_once()
    env = bus.publish.call_args[0][0]
    assert env.correlation_id == "corr-four-pattern"
    assert env.payload["role_count"] == 4
    assert env.payload["displacement_count"] == d_ct
    assert env.payload["augmentation_count"] == a_ct
    assert env.payload["transformation_count"] == t_ct
    assert env.payload["emergence_count"] == e_ct

    assert repo.saved is not None
    assert len(repo.saved) == 4


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


def test_build_period_comparison_compares_skill_mix_across_ordered_periods() -> None:
    snapshots = [
        TemporalPeriodSnapshot(temporal_period="post_gpt4", posting_count=12, skill_mix={"python": 0.7, "sql": 0.3}),
        TemporalPeriodSnapshot(temporal_period="early_genai", posting_count=10, skill_mix={"python": 0.8}),
    ]
    out = build_period_comparison(snapshots)
    assert len(out) == 3
    first = out[0]
    assert first.from_period == "pre_chatgpt"
    assert first.to_period == "early_genai"
    assert first.from_missing is True
    assert first.to_missing is False
    assert first.posting_count_change_ratio is None
    second = out[1]
    assert second.from_period == "early_genai"
    assert second.to_period == "post_gpt4"
    assert second.from_missing is False
    assert second.to_missing is False
    assert second.retained_skill_count == 1
    assert second.added_skill_count == 1
    assert second.removed_skill_count == 0
    assert second.skill_jaccard_similarity == 0.5
    assert second.skill_composition_change_ratio == 0.5
    assert second.posting_count_change_ratio == 0.2


def test_refresh_computes_week8_disruption_signals_for_role() -> None:
    class _SignalsRepo(DisruptionFingerprintRepository):
        def save_fingerprints(self, results, session=None) -> None:
            pass

        def fetch_canonical_roles(self, session=None) -> list[str]:
            return ["role-signals"]

        def fetch_period_snapshots(self, role_id: str, session=None) -> list[TemporalPeriodSnapshot]:
            assert role_id == "role-signals"
            return [
                TemporalPeriodSnapshot(
                    temporal_period="pre_chatgpt",
                    posting_count=10,
                    skill_mix={"python": 0.5, "excel": 0.7},
                    tool_mix={"excel": 1.0},
                    task_mix={"reporting": 0.6, "manual_entry": 0.5},
                    responsibility_density=0.40,
                    ai_requirement_density=0.05,
                ),
                TemporalPeriodSnapshot(
                    temporal_period="early_genai",
                    posting_count=11,
                    skill_mix={"python": 0.6, "excel": 0.6, "prompting": 0.2},
                    tool_mix={"excel": 1.0, "chatgpt": 0.4},
                    task_mix={"reporting": 0.7, "automation_review": 0.3},
                    responsibility_density=0.45,
                    ai_requirement_density=0.10,
                ),
                TemporalPeriodSnapshot(
                    temporal_period="post_gpt4",
                    posting_count=12,
                    skill_mix={"python": 0.7, "prompting": 0.4, "sql": 0.4},
                    tool_mix={"chatgpt": 0.8, "copilot": 0.3},
                    task_mix={"reporting": 0.6, "automation_review": 0.5, "workflow_design": 0.2},
                    responsibility_density=0.50,
                    ai_requirement_density=0.20,
                ),
                TemporalPeriodSnapshot(
                    temporal_period="agentic_era",
                    posting_count=13,
                    skill_mix={"python": 0.8, "prompting": 0.7, "sql": 0.5, "agent_orchestration": 0.3},
                    tool_mix={"chatgpt": 0.9, "copilot": 0.5, "langgraph": 0.2},
                    task_mix={"automation_review": 0.6, "workflow_design": 0.4, "agent_supervision": 0.3},
                    responsibility_density=0.60,
                    ai_requirement_density=0.35,
                ),
            ]

    out = DisruptionFingerprintService(repository=_SignalsRepo()).refresh_disruption_fingerprints()
    fp = out.fingerprints[0]
    assert fp.canonical_role_id == "role-signals"
    assert len(fp.skill_velocity) > 0
    assert fp.skill_velocity[0]["skill_name"] in {"prompting", "python", "agent_orchestration", "excel", "sql"}
    assert len(fp.tool_transition) > 0
    assert any("chatgpt" in t["adopted_tools"] for t in fp.tool_transition)
    assert len(fp.task_shift) == 3
    assert fp.responsibility_expansion > 0
    assert fp.ai_intensity_trend == "increasing"
    assert 0.0 <= fp.workflow_restructuring_score <= 1.0


def test_fingerprints_to_category_counts_multi_label() -> None:
    fps = [
        DisruptionFingerprintRecord(
            canonical_role_id="r1",
            disruption_category=["Displacement", "Augmentation"],
        ),
        DisruptionFingerprintRecord(
            canonical_role_id="r2",
            disruption_category=["Transformation"],
        ),
        DisruptionFingerprintRecord(
            canonical_role_id="r3",
            disruption_category=["Emergence", "Displacement"],
        ),
    ]
    assert _fingerprints_to_category_counts(fps) == (3, 2, 1, 1, 1)


def test_refresh_emits_disruption_refreshed_when_event_bus_configured() -> None:
    bus = MagicMock()
    repo = _FakeRepository()
    svc = DisruptionFingerprintService(repository=repo, event_bus=bus)
    svc.refresh_disruption_fingerprints(session=None, correlation_id="corr-test")
    bus.publish.assert_called_once()
    env = bus.publish.call_args[0][0]
    assert env.correlation_id == "corr-test"
    assert frozenset(env.payload.keys()) == _DISRUPTION_REFRESHED_PAYLOAD_KEYS
    assert env.payload["event_type"] == "DisruptionRefreshed"
    assert env.payload["role_count"] == 2
    assert isinstance(env.payload["refresh_duration_ms"], int)
    assert env.payload["refresh_duration_ms"] >= 0


def test_refresh_emits_zero_counts_when_no_roles_and_bus_configured() -> None:
    bus = MagicMock()
    repo = _EmptyRepository()
    DisruptionFingerprintService(repository=repo, event_bus=bus).refresh_disruption_fingerprints(session=None)
    env = bus.publish.call_args[0][0]
    assert frozenset(env.payload.keys()) == _DISRUPTION_REFRESHED_PAYLOAD_KEYS
    assert env.payload["role_count"] == 0
    assert env.payload["displacement_count"] == 0
    assert env.payload["augmentation_count"] == 0
    assert env.payload["transformation_count"] == 0
    assert env.payload["emergence_count"] == 0
    assert isinstance(env.payload["refresh_duration_ms"], int)
    assert env.payload["refresh_duration_ms"] >= 0


def test_refresh_duration_ms_reflects_perf_counter_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([1000.0, 1001.0])
    monkeypatch.setattr(
        "analytics.disruption.service.time.perf_counter",
        lambda: next(ticks),
    )
    bus = MagicMock()
    repo = _FakeRepository()
    DisruptionFingerprintService(repository=repo, event_bus=bus).refresh_disruption_fingerprints(session=None)
    env = bus.publish.call_args[0][0]
    assert env.payload["refresh_duration_ms"] == 1000


def test_refresh_does_not_publish_when_no_event_bus() -> None:
    module_bus = MagicMock()
    register_disruption_refreshed_bus(module_bus)
    try:
        register_disruption_refreshed_bus(None)
        repo = _FakeRepository()
        DisruptionFingerprintService(repository=repo).refresh_disruption_fingerprints(session=None)
        module_bus.publish.assert_not_called()
    finally:
        register_disruption_refreshed_bus(None)


def test_refresh_instance_event_bus_overrides_module_bus() -> None:
    module_bus = MagicMock()
    instance_bus = MagicMock()
    register_disruption_refreshed_bus(module_bus)
    try:
        repo = _FakeRepository()
        DisruptionFingerprintService(repository=repo, event_bus=instance_bus).refresh_disruption_fingerprints(
            session=None
        )
        instance_bus.publish.assert_called_once()
        module_bus.publish.assert_not_called()
    finally:
        register_disruption_refreshed_bus(None)


class _StubClassifier:
    """Fixed labels per role for refresh-path category count assertions."""

    def classify(self, metrics: RoleDisruptionMetrics) -> list[str]:
        if metrics.canonical_role_id == "role-a":
            return ["Displacement", "Augmentation"]
        return ["Transformation"]


def test_refresh_event_category_counts_match_stub_classifier() -> None:
    bus = MagicMock()
    repo = _FakeRepository()
    svc = DisruptionFingerprintService(repository=repo, event_bus=bus, classifier=_StubClassifier())
    svc.refresh_disruption_fingerprints(session=None)
    env = bus.publish.call_args[0][0]
    assert env.payload["role_count"] == 2
    assert env.payload["displacement_count"] == 1
    assert env.payload["augmentation_count"] == 1
    assert env.payload["transformation_count"] == 1
    assert env.payload["emergence_count"] == 0


def test_refresh_publish_failure_does_not_raise() -> None:
    bus = MagicMock()
    bus.publish.side_effect = RuntimeError("bus down")
    repo = _FakeRepository()
    DisruptionFingerprintService(repository=repo, event_bus=bus).refresh_disruption_fingerprints(session=None)
    bus.publish.assert_called_once()
