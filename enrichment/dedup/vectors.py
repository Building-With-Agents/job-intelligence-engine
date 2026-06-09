"""Embedding vectors: L2-normalize and parse pgvector text for fuzzy dedup."""

from __future__ import annotations

import numpy as np

from common.embedding_vectors import parse_stored_embedding, vector_to_pg_cast_param

__all__ = [
    "cosine_similarity",
    "l2_normalize",
    "parse_stored_embedding",
    "vector_to_pg_cast_param",
]


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(vec))
    if n == 0.0:
        return vec
    return vec / n


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity after L2 normalization (dot product)."""
    aa = l2_normalize(a.astype(np.float64, copy=False))
    bb = l2_normalize(b.astype(np.float64, copy=False))
    return float(np.dot(aa, bb))
