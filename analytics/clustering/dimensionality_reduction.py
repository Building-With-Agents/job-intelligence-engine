"""Dimensionality reduction for embedding matrices before HDBSCAN.

Phase 1 of #327. HDBSCAN density estimation degrades on 1536-D Azure
embeddings (curse of dimensionality); reducing to ~15 dims while preserving
local neighborhood structure restores meaningful density gradients.

UMAP is the default per the BERTopic / top2vec consensus for transformer
embeddings. PCA is wired as an ablation alternative — useful for confirming
that the *technique* matters, not just dimensionality reduction in general.

The function signature is pure (no config reads, no I/O); the pipeline
layer reads accessors and passes resolved values. Keeps this module
testable in isolation.
"""

from __future__ import annotations

import numpy as np

VALID_METHODS = ("none", "pca", "umap")


def reduce_dimensions(
    matrix: np.ndarray,
    *,
    method: str,
    n_components: int,
    umap_n_neighbors: int,
    umap_min_dist: float,
    umap_metric: str,
    umap_random_state: int,
) -> np.ndarray:
    """Project ``matrix`` to ``n_components`` dimensions using ``method``.

    Parameters
    ----------
    matrix
        2-D embedding matrix of shape ``(n_samples, n_features)``. Float dtype.
    method
        One of ``VALID_METHODS``. ``"none"`` returns the input unchanged.
    n_components
        Target dimensionality. Must satisfy ``n_components < min(n_samples, n_features)``.
    umap_n_neighbors, umap_min_dist, umap_metric, umap_random_state
        UMAP-specific kwargs. Ignored when ``method != "umap"``.

    Returns
    -------
    np.ndarray
        Float64 matrix of shape ``(n_samples, n_components)`` (or the input
        unchanged when ``method == "none"``).

    Raises
    ------
    ValueError
        On unknown method or incompatible matrix shape.
    """
    if method not in VALID_METHODS:
        raise ValueError(f"Unknown dimensionality reduction method: {method!r}. Expected one of {VALID_METHODS}.")
    if matrix.ndim != 2:
        raise ValueError(f"matrix must be 2-D; got shape {matrix.shape}")
    n_samples, n_features = matrix.shape

    if method == "none":
        return matrix

    if n_components >= min(n_samples, n_features):
        raise ValueError(f"n_components={n_components} must be < min(n_samples={n_samples}, n_features={n_features})")

    if method == "pca":
        from sklearn.decomposition import PCA  # noqa: PLC0415 — lazy keeps non-PCA paths import-free

        reducer = PCA(n_components=n_components, random_state=umap_random_state)
        return np.asarray(reducer.fit_transform(matrix), dtype=np.float64)

    # method == "umap"
    import umap  # noqa: PLC0415 — lazy keeps the PCA-only path from importing umap+numba

    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=umap_n_neighbors,
        min_dist=umap_min_dist,
        metric=umap_metric,
        random_state=umap_random_state,
    )
    return np.asarray(reducer.fit_transform(matrix), dtype=np.float64)
