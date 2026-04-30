"""Configuration accessors for canonical role clustering.

Reads ``config/clustering.yaml`` via ``common.config_loader``. Legacy env
vars (``CLUSTER_*``, ``EMERGENCE_*``) still override per-accessor during the
deprecation window.

The ``DEFAULT_*`` module constants below are the canonical declaration of
clustering defaults — they MUST mirror the YAML at all times. The accessors
read YAML at runtime (the constants are not used as fallbacks), but the
constants document what the YAML should hold so out-of-band tools, prompts,
and reviewers see one source of truth. Drift between the two is caught by
``analytics/tests/test_clustering_config_parity.py``.
"""

from __future__ import annotations

from common.config_loader import cached_accessor, get_float, get_int, get_str

# Module-level constants are the canonical declaration of clustering defaults.
# They MUST stay in sync with config/clustering.yaml — see
# analytics/tests/test_clustering_config_parity.py which asserts
# DEFAULT_* == <accessor>() for every entry below. The accessors read YAML
# at runtime; these constants document what the YAML should hold.
# When tuning (e.g. issue #321 Tier 2), update BOTH the YAML AND this file.
DEFAULT_CLUSTER_EMBEDDING_BATCH_SIZE = 50
DEFAULT_CLUSTER_MIN_TOTAL_POSTINGS = 500
DEFAULT_CLUSTER_MIN_CLUSTER_SIZE = 5  # #321 Tier 2: was 10 (Tier 1 cosine)
DEFAULT_CLUSTER_MIN_SAMPLES = 5  # #327 Phase 1: reverted 10 → 5; Tier 2's 10 over-tightened in raw 1536-D
DEFAULT_CLUSTER_SELECTION_EPSILON = 0.0
DEFAULT_CLUSTER_SELECTION_METHOD = "leaf"  # #327 Phase 1: "eom" → "leaf" for many fine-grained roles
DEFAULT_CLUSTER_DISTANCE_METRIC = "cosine"
DEFAULT_CLUSTER_LABEL_DOMINANCE_THRESHOLD = 0.30
DEFAULT_CLUSTER_EMBEDDING_AUDIT_AGENT_NAME = "analytics-clustering"

# #327 Phase 1: dimensionality reduction before HDBSCAN.
DEFAULT_CLUSTER_DIM_REDUCTION_METHOD = "umap"
DEFAULT_CLUSTER_DIM_REDUCTION_N_COMPONENTS = 15
DEFAULT_CLUSTER_UMAP_N_NEIGHBORS = 10
DEFAULT_CLUSTER_UMAP_MIN_DIST = 0.0
DEFAULT_CLUSTER_UMAP_METRIC = "cosine"
DEFAULT_CLUSTER_UMAP_RANDOM_STATE = 42

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


@cached_accessor
def cluster_selection_method() -> str:
    return (
        get_str(
            file="clustering",
            key="clustering.selection_method",
            env="CLUSTER_SELECTION_METHOD",
        )
        .strip()
        .lower()
        or DEFAULT_CLUSTER_SELECTION_METHOD
    )


@cached_accessor
def cluster_dim_reduction_method() -> str:
    return (
        get_str(
            file="clustering",
            key="clustering.dimensionality_reduction.method",
            env="CLUSTER_DIM_REDUCTION_METHOD",
        )
        .strip()
        .lower()
        or DEFAULT_CLUSTER_DIM_REDUCTION_METHOD
    )


@cached_accessor
def cluster_dim_reduction_n_components() -> int:
    return get_int(
        file="clustering",
        key="clustering.dimensionality_reduction.n_components",
        env="CLUSTER_DIM_REDUCTION_N_COMPONENTS",
        minimum=1,
    )


@cached_accessor
def cluster_umap_n_neighbors() -> int:
    return get_int(
        file="clustering",
        key="clustering.dimensionality_reduction.umap.n_neighbors",
        env="CLUSTER_UMAP_N_NEIGHBORS",
        minimum=2,
    )


@cached_accessor
def cluster_umap_min_dist() -> float:
    return get_float(
        file="clustering",
        key="clustering.dimensionality_reduction.umap.min_dist",
        env="CLUSTER_UMAP_MIN_DIST",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def cluster_umap_metric() -> str:
    return (
        get_str(
            file="clustering",
            key="clustering.dimensionality_reduction.umap.metric",
            env="CLUSTER_UMAP_METRIC",
        )
        .strip()
        .lower()
        or DEFAULT_CLUSTER_UMAP_METRIC
    )


@cached_accessor
def cluster_umap_random_state() -> int:
    return get_int(
        file="clustering",
        key="clustering.dimensionality_reduction.umap.random_state",
        env="CLUSTER_UMAP_RANDOM_STATE",
        minimum=0,
    )


__all__ = [
    "cluster_dim_reduction_method",
    "cluster_dim_reduction_n_components",
    "cluster_distance_metric",
    "cluster_embedding_audit_agent_name",
    "cluster_embedding_batch_size",
    "cluster_label_dominance_threshold",
    "cluster_min_cluster_size",
    "cluster_min_samples",
    "cluster_min_total_postings",
    "cluster_selection_epsilon",
    "cluster_selection_method",
    "cluster_umap_metric",
    "cluster_umap_min_dist",
    "cluster_umap_n_neighbors",
    "cluster_umap_random_state",
    "DEFAULT_CLUSTER_DIM_REDUCTION_METHOD",
    "DEFAULT_CLUSTER_DIM_REDUCTION_N_COMPONENTS",
    "DEFAULT_CLUSTER_DISTANCE_METRIC",
    "DEFAULT_CLUSTER_EMBEDDING_BATCH_SIZE",
    "DEFAULT_CLUSTER_EMBEDDING_AUDIT_AGENT_NAME",
    "DEFAULT_CLUSTER_LABEL_DOMINANCE_THRESHOLD",
    "DEFAULT_CLUSTER_MIN_CLUSTER_SIZE",
    "DEFAULT_CLUSTER_MIN_SAMPLES",
    "DEFAULT_CLUSTER_MIN_TOTAL_POSTINGS",
    "DEFAULT_CLUSTER_SELECTION_EPSILON",
    "DEFAULT_CLUSTER_SELECTION_METHOD",
    "DEFAULT_CLUSTER_UMAP_METRIC",
    "DEFAULT_CLUSTER_UMAP_MIN_DIST",
    "DEFAULT_CLUSTER_UMAP_N_NEIGHBORS",
    "DEFAULT_CLUSTER_UMAP_RANDOM_STATE",
    "DEFAULT_EMERGENCE_MIN_DISTINCT_EMPLOYERS",
    "DEFAULT_EMERGENCE_MIN_NOVEL_SKILLS",
    "DEFAULT_EMERGENCE_MIN_QUALITY_SCORE",
    "emergence_min_distinct_employers",
    "emergence_min_novel_skills",
    "emergence_min_quality_score",
]
