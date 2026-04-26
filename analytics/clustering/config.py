"""Configuration accessors for canonical role clustering.

Reads ``config/clustering.yaml`` via ``common.config_loader``. Legacy env
vars (``CLUSTER_*``, ``EMERGENCE_*``) still override per-accessor during the
deprecation window. Defaults live in YAML — there are none in this file.
"""

from __future__ import annotations

from common.config_loader import cached_accessor, get_float, get_int, get_str

# Module-level constants kept for backwards compatibility with existing
# imports (analytics/clustering/__init__.py re-exports these). Values mirror
# the YAML defaults; they are NOT used as fallbacks — the loader reads YAML.
DEFAULT_CLUSTER_EMBEDDING_BATCH_SIZE = 50
DEFAULT_CLUSTER_MIN_TOTAL_POSTINGS = 500
DEFAULT_CLUSTER_MIN_CLUSTER_SIZE = 10
DEFAULT_CLUSTER_MIN_SAMPLES = 5
DEFAULT_CLUSTER_SELECTION_EPSILON = 0.0
DEFAULT_CLUSTER_DISTANCE_METRIC = "euclidean"
DEFAULT_CLUSTER_LABEL_DOMINANCE_THRESHOLD = 0.30
DEFAULT_CLUSTER_EMBEDDING_AUDIT_AGENT_NAME = "analytics-clustering"

DEFAULT_EMERGENCE_MIN_QUALITY_SCORE = 0.70
DEFAULT_EMERGENCE_MIN_NOVEL_SKILLS = 3
DEFAULT_EMERGENCE_MIN_DISTINCT_EMPLOYERS = 2


@cached_accessor
def cluster_embedding_batch_size() -> int:
    return get_int(
        file="clustering",
        key="clustering.embedding.batch_size",
        env="CLUSTER_EMBEDDING_BATCH_SIZE",
        minimum=1,
    )


@cached_accessor
def cluster_embedding_audit_agent_name() -> str:
    return (
        get_str(
            file="clustering",
            key="clustering.embedding.audit_agent_name",
            env="CLUSTER_EMBEDDING_AUDIT_AGENT_NAME",
        ).strip()
        or DEFAULT_CLUSTER_EMBEDDING_AUDIT_AGENT_NAME
    )


@cached_accessor
def cluster_min_total_postings() -> int:
    return get_int(
        file="clustering",
        key="clustering.min_total_postings",
        env="CLUSTER_MIN_TOTAL_POSTINGS",
        minimum=1,
    )


@cached_accessor
def cluster_min_cluster_size() -> int:
    return get_int(
        file="clustering",
        key="clustering.min_cluster_size",
        env="CLUSTER_MIN_CLUSTER_SIZE",
        minimum=1,
    )


@cached_accessor
def cluster_min_samples() -> int:
    return get_int(
        file="clustering",
        key="clustering.min_samples",
        env="CLUSTER_MIN_SAMPLES",
        minimum=1,
    )


@cached_accessor
def cluster_selection_epsilon() -> float:
    return get_float(
        file="clustering",
        key="clustering.selection_epsilon",
        env="CLUSTER_SELECTION_EPSILON",
        minimum=0.0,
    )


@cached_accessor
def cluster_distance_metric() -> str:
    return (
        get_str(
            file="clustering",
            key="clustering.distance_metric",
            env="CLUSTER_DISTANCE_METRIC",
        )
        .strip()
        .lower()
        or DEFAULT_CLUSTER_DISTANCE_METRIC
    )


@cached_accessor
def cluster_label_dominance_threshold() -> float:
    return get_float(
        file="clustering",
        key="clustering.label_dominance_threshold",
        env="CLUSTER_LABEL_DOMINANCE_THRESHOLD",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def emergence_min_quality_score() -> float:
    return get_float(
        file="clustering",
        key="clustering.emergence.min_quality_score",
        env="EMERGENCE_MIN_QUALITY_SCORE",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def emergence_min_novel_skills() -> int:
    return get_int(
        file="clustering",
        key="clustering.emergence.min_novel_skills",
        env="EMERGENCE_MIN_NOVEL_SKILLS",
        minimum=1,
    )


@cached_accessor
def emergence_min_distinct_employers() -> int:
    return get_int(
        file="clustering",
        key="clustering.emergence.min_distinct_employers",
        env="EMERGENCE_MIN_DISTINCT_EMPLOYERS",
        minimum=1,
    )


__all__ = [
    "cluster_distance_metric",
    "cluster_embedding_audit_agent_name",
    "cluster_embedding_batch_size",
    "cluster_label_dominance_threshold",
    "cluster_min_cluster_size",
    "cluster_min_samples",
    "cluster_min_total_postings",
    "cluster_selection_epsilon",
    "DEFAULT_CLUSTER_DISTANCE_METRIC",
    "DEFAULT_CLUSTER_EMBEDDING_BATCH_SIZE",
    "DEFAULT_CLUSTER_EMBEDDING_AUDIT_AGENT_NAME",
    "DEFAULT_CLUSTER_LABEL_DOMINANCE_THRESHOLD",
    "DEFAULT_CLUSTER_MIN_CLUSTER_SIZE",
    "DEFAULT_CLUSTER_MIN_SAMPLES",
    "DEFAULT_CLUSTER_MIN_TOTAL_POSTINGS",
    "DEFAULT_CLUSTER_SELECTION_EPSILON",
    "DEFAULT_EMERGENCE_MIN_DISTINCT_EMPLOYERS",
    "DEFAULT_EMERGENCE_MIN_NOVEL_SKILLS",
    "DEFAULT_EMERGENCE_MIN_QUALITY_SCORE",
    "emergence_min_distinct_employers",
    "emergence_min_novel_skills",
    "emergence_min_quality_score",
]
