"""Unit tests for canonical role loader/persist wiring (no live DB)."""

from __future__ import annotations

from unittest.mock import MagicMock

from analytics.canonical_roles.loader import row_to_features
from analytics.canonical_roles.persist import (
    _build_cluster_role_ids,
    cleanup_orphan_canonical_roles,
    persist_clustering_result,
)
from analytics.clustering.types import ClusteringResult, ClusterSummary
from common.data_store.models import CanonicalRole


def _make_cluster(
    cluster_id: str,
    *,
    label: str,
    raw_cluster_label: int = 1,
) -> ClusterSummary:
    return ClusterSummary(
        cluster_id=cluster_id,
        raw_cluster_label=raw_cluster_label,
        label=label,
        label_source="dominant_title",
        member_posting_ids=["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
        member_count=1,
        representative_titles=[label],
    )


def test_row_to_features_parses_skill_name_and_label_fallback() -> None:
    row = {
        "job_posting_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "title": "Software Engineer",
        "company_id": "cccccccc-dddd-eeee-ffff-000000000001",
        "company_name": "Acme",
        "quality_score": 0.85,
        "seniority": "mid",
        "skills": [{"skill_name": "Go"}, {"label": "LegacySkill"}],
        "tools": [{"tool_name": "Git"}],
        "responsibilities": [{"responsibility_description": "Ship features"}],
    }
    f = row_to_features(row)
    assert f.posting_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert f.skills == ["Go", "LegacySkill"]
    assert f.tools == ["Git"]
    assert f.responsibilities == ["Ship features"]
    assert f.quality_score == 0.85
    assert f.seniority == "mid"


def test_persist_skipped_no_session_add() -> None:
    session = MagicMock()
    result = ClusteringResult(
        assignments=[],
        clusters=[],
        emergence_candidates=[],
        total_input_postings=10,
        eligible_posting_count=0,
        clustered_posting_count=0,
        noise_posting_count=0,
        skipped=True,
        skip_reason="insufficient_total_postings",
    )
    out = persist_clustering_result(session, result, correlation_id="test-corr")
    assert out["cluster_id_to_role_id"] == {}
    assert out["roles_inserted"] == 0
    session.add.assert_not_called()


def test_build_cluster_role_ids_stable_when_same_role_recomputed() -> None:
    first = _make_cluster("cluster-0001", label="Data Engineer", raw_cluster_label=7)
    second = _make_cluster("cluster-0009", label="Data Engineer", raw_cluster_label=42)

    first_ids = _build_cluster_role_ids([first])
    second_ids = _build_cluster_role_ids([second])

    assert first_ids["cluster-0001"] == second_ids["cluster-0009"]


def test_persist_reuses_existing_role_id_instead_of_adding_duplicate() -> None:
    cluster = _make_cluster("cluster-0001", label="Data Engineer")
    expected_role_id = _build_cluster_role_ids([cluster])["cluster-0001"]
    existing_role = CanonicalRole(
        role_id=expected_role_id,
        label="Old Label",
        representative_titles=["Old Label"],
    )

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [existing_role]

    session = MagicMock()
    session.execute.return_value = execute_result

    result = ClusteringResult(
        assignments=[],
        clusters=[cluster],
        emergence_candidates=[],
        total_input_postings=1,
        eligible_posting_count=1,
        clustered_posting_count=1,
        noise_posting_count=0,
        skipped=False,
        skip_reason=None,
    )

    out = persist_clustering_result(session, result, correlation_id="test-corr")

    assert out["cluster_id_to_role_id"]["cluster-0001"] == expected_role_id
    assert out["roles_inserted"] == 0
    assert existing_role.label == "Data Engineer"
    session.add.assert_not_called()


def test_cleanup_orphan_canonical_roles_checks_snapshots() -> None:
    session = MagicMock()
    session.execute.return_value.rowcount = 2

    deleted = cleanup_orphan_canonical_roles(session)

    assert deleted == 2
    all_stmts = " ".join(str(call.args[0]) for call in session.execute.call_args_list)
    assert "role_snapshot_weekly" in all_stmts


def test_cleanup_orphan_deletes_stale_snapshots_before_roles() -> None:
    """Stale snapshot rows must be removed first so orphan roles are not shielded.

    Reproduces #334: a canonical role has ``posting_count > 0`` (cached) but
    zero actual FK references from ``job_postings``.  Old
    ``role_snapshot_weekly`` rows keep it alive unless we clean them first.
    """
    call_count = 0
    stale_snapshot_rowcount = 3
    orphan_role_rowcount = 1

    def _execute_side_effect(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.rowcount = stale_snapshot_rowcount
        else:
            result.rowcount = orphan_role_rowcount
        return result

    session = MagicMock()
    session.execute.side_effect = _execute_side_effect

    deleted = cleanup_orphan_canonical_roles(session)

    assert deleted == orphan_role_rowcount
    assert session.execute.call_count == 2
    first_stmt = str(session.execute.call_args_list[0].args[0])
    second_stmt = str(session.execute.call_args_list[1].args[0])
    assert "role_snapshot_weekly" in first_stmt
    assert "DELETE" in first_stmt
    assert "canonical_roles" in second_stmt


def test_persist_sync_writes_label_embedding_via_raw_sql() -> None:
    """After flush, label_embedding UPDATE uses CAST(:vec AS vector)."""
    vec = [0.01] * 1536
    cluster = ClusterSummary(
        cluster_id="cluster-0001",
        raw_cluster_label=1,
        label="Data Engineer",
        label_source="dominant_title",
        member_posting_ids=["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
        member_count=1,
        representative_titles=["Data Engineer"],
        centroid_embedding=vec,
    )
    expected_role_id = _build_cluster_role_ids([cluster])["cluster-0001"]
    existing_role = CanonicalRole(
        role_id=expected_role_id,
        label="Old Label",
        representative_titles=["Old Label"],
    )

    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = [existing_role]
    update_result = MagicMock()

    session = MagicMock()
    session.execute.side_effect = [select_result, update_result]

    result = ClusteringResult(
        assignments=[],
        clusters=[cluster],
        emergence_candidates=[],
        total_input_postings=1,
        eligible_posting_count=1,
        clustered_posting_count=1,
        noise_posting_count=0,
        skipped=False,
        skip_reason=None,
    )

    persist_clustering_result(session, result, correlation_id="test-corr")

    label_sqls = [
        str(call.args[0])
        for call in session.execute.call_args_list
        if call.args and "label_embedding" in str(call.args[0])
    ]
    assert len(label_sqls) == 1
    assert "label_embedding" in label_sqls[0]
    assert "CAST(:vec AS vector)" in label_sqls[0]
