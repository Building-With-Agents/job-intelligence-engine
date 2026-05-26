"""Persist noise-row centroid fallback (JIE #363)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from analytics.canonical_roles.persist import persist_clustering_result
from analytics.clustering.types import ClusteredPosting, ClusteringResult, ClusterSummary


def _noise_result() -> ClusteringResult:
    cluster = ClusterSummary(
        cluster_id="cluster-0001",
        raw_cluster_label=1,
        label="Data Engineer",
        label_source="dominant_title",
        member_posting_ids=["bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeffff"],
        member_count=1,
        representative_titles=["Data Engineer"],
        centroid_embedding=[0.1] * 1536,
    )
    return ClusteringResult(
        assignments=[
            ClusteredPosting(
                posting_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                is_noise=True,
            ),
            ClusteredPosting(
                posting_id="bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeffff",
                cluster_id="cluster-0001",
                raw_cluster_label=1,
                cluster_label="Data Engineer",
            ),
        ],
        clusters=[cluster],
        emergence_candidates=[],
        total_input_postings=2,
        eligible_posting_count=2,
        clustered_posting_count=1,
        noise_posting_count=1,
        skipped=False,
        skip_reason=None,
    )


@patch("analytics.canonical_roles.persist.nearest_canonical_role_id", return_value="fallback-role-id")
def test_noise_row_gets_fallback_role_when_similarity_high(mock_nearest: MagicMock) -> None:
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    session.execute.return_value = execute_result

    embeddings = {"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee": [0.2] * 1536}
    persist_clustering_result(
        session,
        _noise_result(),
        correlation_id="test-corr",
        posting_embeddings=embeddings,
    )

    calls = [str(c.args[0]) if c.args else str(c.kwargs) for c in session.execute.call_args_list]
    noise_assign = any("canonical_role_id = :rid" in c for c in calls)
    assert noise_assign
    mock_nearest.assert_called_once()


@patch("analytics.canonical_roles.persist.nearest_canonical_role_id", return_value=None)
def test_noise_row_stays_null_when_below_threshold(mock_nearest: MagicMock) -> None:
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    session.execute.return_value = execute_result

    embeddings = {"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee": [0.2] * 1536}
    persist_clustering_result(
        session,
        _noise_result(),
        correlation_id="test-corr",
        posting_embeddings=embeddings,
    )

    calls = [str(c.args[0]) if c.args else "" for c in session.execute.call_args_list]
    assert any("canonical_role_id = NULL" in c for c in calls)
    mock_nearest.assert_called_once()
