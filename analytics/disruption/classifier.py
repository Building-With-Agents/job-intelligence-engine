"""Map role-level metrics to disruption pattern labels."""

from __future__ import annotations

import os

from analytics.disruption.models import RoleDisruptionMetrics

_PATTERN_ORDER = ("Displacement", "Augmentation", "Transformation", "Emergence")
_AI_TOOL_KEYWORDS = (
    "ai",
    "gpt",
    "llm",
    "copilot",
    "agent",
    "prompt",
    "claude",
    "openai",
    "anthropic",
    "langchain",
    "langgraph",
)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


TRANSFORMATION_SKILL_CHANGE_THRESHOLD = _float_env("DISRUPTION_TRANSFORMATION_SKILL_CHANGE_THRESHOLD", 0.30)
DEMAND_DECLINE_THRESHOLD = _float_env("DISRUPTION_DEMAND_DECLINE_THRESHOLD", -0.10)
DEMAND_STABLE_BAND = _float_env("DISRUPTION_DEMAND_STABLE_BAND", 0.10)
EMERGENCE_AI_DENSITY_THRESHOLD = _float_env("DISRUPTION_EMERGENCE_AI_DENSITY_THRESHOLD", 0.25)
AI_DENSITY_INCREASE_THRESHOLD = _float_env("DISRUPTION_AI_DENSITY_INCREASE_THRESHOLD", 0.05)


class DisruptionClassifier:
    """Classify ``RoleDisruptionMetrics`` into disruption category strings."""

    def classify(self, metrics: RoleDisruptionMetrics) -> list[str]:
        """Return disruption pattern labels for ``metrics``.

        Applies Week 8 rule set with sparse-period-safe defaults:
        - Transformation: skill composition change >= threshold
        - Displacement: declining demand + automation signals
        - Augmentation: stable/growing demand + new AI tools
        - Emergence: no pre-chatgpt baseline + high AI density

        Args:
            metrics: Aggregated signals for one canonical role.

        Returns:
            Ordered list of category labels.
        """
        labels: set[str] = set()
        demand_change = _demand_change_ratio(metrics)
        ai_density_change = _ai_density_change(metrics)
        latest_ai_density = _latest_ai_density(metrics)
        ai_tools_added = _has_new_ai_tools(metrics)
        average_skill_shift = _average_skill_shift(metrics)
        pre_baseline_missing = _is_pre_chatgpt_baseline_missing(metrics)

        if average_skill_shift >= TRANSFORMATION_SKILL_CHANGE_THRESHOLD:
            labels.add("Transformation")

        automation_signal = (
            ai_density_change >= AI_DENSITY_INCREASE_THRESHOLD
            or ai_tools_added
            or _responsibility_scope_contracted(metrics)
        )
        if demand_change <= DEMAND_DECLINE_THRESHOLD and automation_signal:
            labels.add("Displacement")

        if demand_change >= (-1 * DEMAND_STABLE_BAND) and ai_tools_added:
            labels.add("Augmentation")

        if pre_baseline_missing and latest_ai_density >= EMERGENCE_AI_DENSITY_THRESHOLD:
            labels.add("Emergence")

        return [label for label in _PATTERN_ORDER if label in labels]


def _observed_snapshots(metrics: RoleDisruptionMetrics):
    return tuple(s for s in metrics.snapshots if s.has_observed_data)


def _demand_change_ratio(metrics: RoleDisruptionMetrics) -> float:
    snapshots = _observed_snapshots(metrics)
    if len(snapshots) < 2:
        return 0.0
    start = snapshots[0].posting_count
    end = snapshots[-1].posting_count
    if start <= 0:
        return 0.0
    return (end - start) / start


def _ai_density_change(metrics: RoleDisruptionMetrics) -> float:
    snapshots = _observed_snapshots(metrics)
    if len(snapshots) < 2:
        return 0.0
    return snapshots[-1].ai_requirement_density - snapshots[0].ai_requirement_density


def _latest_ai_density(metrics: RoleDisruptionMetrics) -> float:
    snapshots = _observed_snapshots(metrics)
    if not snapshots:
        return 0.0
    return snapshots[-1].ai_requirement_density


def _responsibility_scope_contracted(metrics: RoleDisruptionMetrics) -> bool:
    snapshots = _observed_snapshots(metrics)
    if len(snapshots) < 2:
        return False
    return snapshots[-1].responsibility_density < snapshots[0].responsibility_density


def _is_ai_tool(tool_name: str) -> bool:
    lowered = (tool_name or "").strip().lower()
    return any(k in lowered for k in _AI_TOOL_KEYWORDS)


def _has_new_ai_tools(metrics: RoleDisruptionMetrics) -> bool:
    snapshots = _observed_snapshots(metrics)
    if len(snapshots) < 2:
        return False
    first_tools = set(snapshots[0].tool_mix.keys())
    latest_tools = set(snapshots[-1].tool_mix.keys())
    adopted = latest_tools - first_tools
    return any(_is_ai_tool(name) for name in adopted)


def _average_skill_shift(metrics: RoleDisruptionMetrics) -> float:
    valid = [
        cmp.skill_composition_change_ratio
        for cmp in metrics.period_comparison
        if not cmp.from_missing and not cmp.to_missing
    ]
    if valid:
        return sum(valid) / len(valid)

    # Sparse-series fallback: compare first and last observed skill sets directly.
    snapshots = _observed_snapshots(metrics)
    if len(snapshots) < 2:
        return 0.0
    first_skills = set(snapshots[0].skill_mix.keys())
    last_skills = set(snapshots[-1].skill_mix.keys())
    union = first_skills | last_skills
    if not union:
        return 0.0
    intersection = first_skills & last_skills
    return 1.0 - (len(intersection) / len(union))


def _is_pre_chatgpt_baseline_missing(metrics: RoleDisruptionMetrics) -> bool:
    for snapshot in metrics.snapshots:
        if snapshot.temporal_period != "pre_chatgpt":
            continue
        return (not snapshot.has_observed_data) or snapshot.posting_count <= 0
    return True
