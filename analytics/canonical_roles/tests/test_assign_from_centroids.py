"""Unit tests for centroid-based canonical role assignment (JIE #363)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from analytics.canonical_roles.assign_from_centroids import (
    build_assignment_text,
    nearest_canonical_role_match,
)
from analytics.clustering.types import PostingClusterFeatures


def _features() -> PostingClusterFeatures:
    return PostingClusterFeatures(
        posting_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        title="Data Engineer",
        skills=["Python"],
        tools=["Git"],
        responsibilities=["Build pipelines"],
        seniority="mid",
    )


def test_build_assignment_text_matches_clustering_shape() -> None:
    text = build_assignment_text(_features())
    assert "title: Data Engineer" in text
    assert "skills: Python" in text


def test_nearest_canonical_role_match_above_threshold() -> None:
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = (
        "role-uuid-1",
        "Data Engineer",
        0.82,
    )
    role_id, label, sim = nearest_canonical_role_match([0.1] * 1536, session, min_similarity=0.75)
    assert role_id == "role-uuid-1"
    assert label == "Data Engineer"
    assert sim == pytest.approx(0.82)


def test_nearest_canonical_role_match_below_threshold() -> None:
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = (
        "role-uuid-1",
        "Data Engineer",
        0.70,
    )
    role_id, _label, sim = nearest_canonical_role_match([0.1] * 1536, session, min_similarity=0.75)
    assert role_id is None
    assert sim == pytest.approx(0.70)


def test_nearest_canonical_role_match_wrong_embedding_dim() -> None:
    session = MagicMock()
    role_id, label, sim = nearest_canonical_role_match([0.1] * 10, session, min_similarity=0.75)
    assert role_id is None
    assert label is None
    assert sim is None
    session.execute.assert_not_called()
