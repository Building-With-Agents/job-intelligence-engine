"""Parity guard: canonical Python DEFAULT_* constants must match the YAML
accessors at runtime.

Issue #321 (Tier 2 clustering tuning) exposed a class of bug where someone
updates ``config/clustering.yaml`` but forgets the canonical declarations in
``analytics/clustering/config.py``. The Python constants are not a fallback
— the loader reads YAML directly — but they document the authoritative
values, so silent drift between the two leaves the codebase claiming one
thing while running another.

This test pins them together. If you tune a clustering value, update BOTH
the YAML and the DEFAULT_* constant; this test catches the case where you
update only one.
"""

from __future__ import annotations

import pytest

from analytics.clustering.config import (
    DEFAULT_CLUSTER_DIM_REDUCTION_METHOD,
    DEFAULT_CLUSTER_DIM_REDUCTION_N_COMPONENTS,
    DEFAULT_CLUSTER_DISTANCE_METRIC,
    DEFAULT_CLUSTER_EMBEDDING_AUDIT_AGENT_NAME,
    DEFAULT_CLUSTER_EMBEDDING_BATCH_SIZE,
    DEFAULT_CLUSTER_LABEL_DOMINANCE_THRESHOLD,
    DEFAULT_CLUSTER_MIN_CLUSTER_SIZE,
    DEFAULT_CLUSTER_MIN_SAMPLES,
    DEFAULT_CLUSTER_MIN_TOTAL_POSTINGS,
    DEFAULT_CLUSTER_SELECTION_EPSILON,
    DEFAULT_CLUSTER_SELECTION_METHOD,
    DEFAULT_CLUSTER_UMAP_METRIC,
    DEFAULT_CLUSTER_UMAP_MIN_DIST,
    DEFAULT_CLUSTER_UMAP_N_NEIGHBORS,
    DEFAULT_CLUSTER_UMAP_RANDOM_STATE,
    DEFAULT_EMERGENCE_MIN_DISTINCT_EMPLOYERS,
    DEFAULT_EMERGENCE_MIN_NOVEL_SKILLS,
    DEFAULT_EMERGENCE_MIN_QUALITY_SCORE,
    cluster_dim_reduction_method,
    cluster_dim_reduction_n_components,
    cluster_distance_metric,
    cluster_embedding_audit_agent_name,
    cluster_embedding_batch_size,
    cluster_label_dominance_threshold,
    cluster_min_cluster_size,
    cluster_min_samples,
    cluster_min_total_postings,
    cluster_selection_epsilon,
    cluster_selection_method,
    cluster_umap_metric,
    cluster_umap_min_dist,
    cluster_umap_n_neighbors,
    cluster_umap_random_state,
    emergence_min_distinct_employers,
    emergence_min_novel_skills,
    emergence_min_quality_score,
)


@pytest.fixture
def yaml_only_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every CLUSTER_*/EMERGENCE_* env var so accessors must read YAML."""
    for var in (
        "CLUSTER_EMBEDDING_BATCH_SIZE",
        "CLUSTER_EMBEDDING_AUDIT_AGENT_NAME",
        "CLUSTER_MIN_TOTAL_POSTINGS",
        "CLUSTER_MIN_CLUSTER_SIZE",
        "CLUSTER_MIN_SAMPLES",
        "CLUSTER_SELECTION_EPSILON",
        "CLUSTER_SELECTION_METHOD",
        "CLUSTER_DISTANCE_METRIC",
        "CLUSTER_LABEL_DOMINANCE_THRESHOLD",
        "CLUSTER_DIM_REDUCTION_METHOD",
        "CLUSTER_DIM_REDUCTION_N_COMPONENTS",
        "CLUSTER_UMAP_N_NEIGHBORS",
        "CLUSTER_UMAP_MIN_DIST",
        "CLUSTER_UMAP_METRIC",
        "CLUSTER_UMAP_RANDOM_STATE",
        "EMERGENCE_MIN_QUALITY_SCORE",
        "EMERGENCE_MIN_NOVEL_SKILLS",
        "EMERGENCE_MIN_DISTINCT_EMPLOYERS",
    ):
        monkeypatch.delenv(var, raising=False)
    # cached_accessor memoizes; clear so the YAML reads fresh under the
    # cleared env. Each accessor exposes .cache_clear via functools.lru_cache.
    for fn in (
        cluster_embedding_batch_size,
        cluster_embedding_audit_agent_name,
        cluster_min_total_postings,
        cluster_min_cluster_size,
        cluster_min_samples,
        cluster_selection_epsilon,
        cluster_selection_method,
        cluster_distance_metric,
        cluster_label_dominance_threshold,
        cluster_dim_reduction_method,
        cluster_dim_reduction_n_components,
        cluster_umap_n_neighbors,
        cluster_umap_min_dist,
        cluster_umap_metric,
        cluster_umap_random_state,
        emergence_min_quality_score,
        emergence_min_novel_skills,
        emergence_min_distinct_employers,
    ):
        cache_clear = getattr(fn, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()


@pytest.mark.parametrize(
    ("constant", "accessor"),
    [
        (DEFAULT_CLUSTER_EMBEDDING_BATCH_SIZE, cluster_embedding_batch_size),
        (DEFAULT_CLUSTER_EMBEDDING_AUDIT_AGENT_NAME, cluster_embedding_audit_agent_name),
        (DEFAULT_CLUSTER_MIN_TOTAL_POSTINGS, cluster_min_total_postings),
        (DEFAULT_CLUSTER_MIN_CLUSTER_SIZE, cluster_min_cluster_size),
        (DEFAULT_CLUSTER_MIN_SAMPLES, cluster_min_samples),
        (DEFAULT_CLUSTER_SELECTION_EPSILON, cluster_selection_epsilon),
        (DEFAULT_CLUSTER_SELECTION_METHOD, cluster_selection_method),
        (DEFAULT_CLUSTER_DISTANCE_METRIC, cluster_distance_metric),
        (DEFAULT_CLUSTER_LABEL_DOMINANCE_THRESHOLD, cluster_label_dominance_threshold),
        (DEFAULT_CLUSTER_DIM_REDUCTION_METHOD, cluster_dim_reduction_method),
        (DEFAULT_CLUSTER_DIM_REDUCTION_N_COMPONENTS, cluster_dim_reduction_n_components),
        (DEFAULT_CLUSTER_UMAP_N_NEIGHBORS, cluster_umap_n_neighbors),
        (DEFAULT_CLUSTER_UMAP_MIN_DIST, cluster_umap_min_dist),
        (DEFAULT_CLUSTER_UMAP_METRIC, cluster_umap_metric),
        (DEFAULT_CLUSTER_UMAP_RANDOM_STATE, cluster_umap_random_state),
        (DEFAULT_EMERGENCE_MIN_QUALITY_SCORE, emergence_min_quality_score),
        (DEFAULT_EMERGENCE_MIN_NOVEL_SKILLS, emergence_min_novel_skills),
        (DEFAULT_EMERGENCE_MIN_DISTINCT_EMPLOYERS, emergence_min_distinct_employers),
    ],
)
def test_default_constant_matches_yaml_accessor(yaml_only_env: None, constant: object, accessor: object) -> None:
    assert callable(accessor)
    assert constant == accessor(), (
        f"Canonical DEFAULT_* constant in analytics/clustering/config.py is out of sync with "
        f"config/clustering.yaml. {accessor.__name__}() returned {accessor()!r}, "
        f"but the constant declares {constant!r}. Update both together."
    )
