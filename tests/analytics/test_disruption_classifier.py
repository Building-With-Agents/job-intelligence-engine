"""Unit tests for Week 8 disruption pattern classifier (#106)."""

from __future__ import annotations

from analytics.disruption.classifier import DisruptionClassifier
from analytics.disruption.models import (
    RoleDisruptionMetrics,
    TemporalPeriodSnapshot,
    build_period_comparison,
    normalize_temporal_snapshots,
)


def _metrics(snapshots: list[TemporalPeriodSnapshot]) -> RoleDisruptionMetrics:
    normalized = normalize_temporal_snapshots(snapshots)
    return RoleDisruptionMetrics(
        canonical_role_id="role-x",
        snapshots=normalized,
        period_comparison=build_period_comparison(normalized),
    )


def test_classify_transformation_when_skill_shift_exceeds_threshold() -> None:
    metrics = _metrics(
        [
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
        ]
    )
    labels = DisruptionClassifier().classify(metrics)
    assert "Transformation" in labels


def test_classify_displacement_when_demand_declines_and_automation_rises() -> None:
    metrics = _metrics(
        [
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
        ]
    )
    labels = DisruptionClassifier().classify(metrics)
    assert "Displacement" in labels


def test_classify_augmentation_when_demand_stable_and_ai_tools_added() -> None:
    metrics = _metrics(
        [
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
        ]
    )
    labels = DisruptionClassifier().classify(metrics)
    assert "Augmentation" in labels


def test_classify_emergence_when_no_prebaseline_and_high_ai_density() -> None:
    metrics = _metrics(
        [
            TemporalPeriodSnapshot(
                temporal_period="agentic_era",
                posting_count=25,
                tool_mix={"langgraph": 0.7, "gpt-4": 0.9},
                ai_requirement_density=0.4,
            ),
        ]
    )
    labels = DisruptionClassifier().classify(metrics)
    assert "Emergence" in labels


def test_classifier_supports_multi_pattern_output() -> None:
    metrics = _metrics(
        [
            TemporalPeriodSnapshot(
                temporal_period="pre_chatgpt",
                posting_count=120,
                skill_mix={"excel": 1.0, "manual_entry": 0.9},
                tool_mix={"excel": 1.0},
                responsibility_density=0.7,
                ai_requirement_density=0.02,
            ),
            TemporalPeriodSnapshot(
                temporal_period="agentic_era",
                posting_count=100,
                skill_mix={"python": 0.8, "prompting": 0.8, "agent_supervision": 0.4},
                tool_mix={"chatgpt": 0.9, "langgraph": 0.4},
                responsibility_density=0.55,
                ai_requirement_density=0.35,
            ),
        ]
    )
    labels = DisruptionClassifier().classify(metrics)
    assert "Transformation" in labels
    assert "Displacement" in labels


def test_classifier_supports_augmentation_and_transformation_together() -> None:
    metrics = _metrics(
        [
            TemporalPeriodSnapshot(
                temporal_period="pre_chatgpt",
                posting_count=100,
                skill_mix={"excel": 1.0, "reporting": 0.8},
                tool_mix={"excel": 1.0},
                ai_requirement_density=0.02,
            ),
            TemporalPeriodSnapshot(
                temporal_period="agentic_era",
                posting_count=106,
                skill_mix={"python": 0.8, "prompting": 0.8, "agent_orchestration": 0.4},
                tool_mix={"excel": 0.6, "copilot": 0.6, "chatgpt": 0.8},
                ai_requirement_density=0.3,
            ),
        ]
    )
    labels = DisruptionClassifier().classify(metrics)
    assert "Augmentation" in labels
    assert "Transformation" in labels


def test_classifier_handles_missing_early_period_data_without_crashing() -> None:
    metrics = _metrics(
        [
            TemporalPeriodSnapshot(
                temporal_period="post_gpt4",
                posting_count=20,
                skill_mix={"python": 0.7},
                tool_mix={"chatgpt": 0.5},
                ai_requirement_density=0.2,
            ),
            TemporalPeriodSnapshot(
                temporal_period="agentic_era",
                posting_count=22,
                skill_mix={"python": 0.8, "prompting": 0.5},
                tool_mix={"chatgpt": 0.8, "copilot": 0.4},
                ai_requirement_density=0.3,
            ),
        ]
    )
    labels = DisruptionClassifier().classify(metrics)
    assert isinstance(labels, list)
    assert "Augmentation" in labels


def test_transformation_threshold_is_configurable_via_env(monkeypatch) -> None:
    metrics = _metrics(
        [
            TemporalPeriodSnapshot(
                temporal_period="pre_chatgpt",
                posting_count=100,
                skill_mix={"excel": 1.0, "reporting": 1.0},
            ),
            TemporalPeriodSnapshot(
                temporal_period="agentic_era",
                posting_count=100,
                skill_mix={"excel": 1.0, "python": 0.4},
            ),
        ]
    )
    # Approx shift here is 0.666..., so raise threshold above that to suppress Transformation.
    monkeypatch.setenv("DISRUPTION_TRANSFORMATION_SKILL_CHANGE_THRESHOLD", "0.8")
    labels = DisruptionClassifier().classify(metrics)
    assert "Transformation" not in labels

    # Lower threshold and verify same metrics now classify as Transformation.
    monkeypatch.setenv("DISRUPTION_TRANSFORMATION_SKILL_CHANGE_THRESHOLD", "0.3")
    labels_low = DisruptionClassifier().classify(metrics)
    assert "Transformation" in labels_low
