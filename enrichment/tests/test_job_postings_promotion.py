"""Unit tests for enrichment promotion helpers."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from enrichment.dedup.types import FuzzyDedupResult
from enrichment.job_postings_promotion import (
    apply_enrichment_to_job_postings,
    apply_fuzzy_dedup_result,
)

CURRENT_ID = "00000000-0000-0000-0000-000000000001"
MATCHED_ID = "00000000-0000-0000-0000-000000000002"
CLUSTER_ID = "00000000-0000-0000-0000-0000000000aa"


def _mapping_first(row: dict | None) -> MagicMock:
    m = MagicMock()
    m.mappings.return_value.first.return_value = row
    return m


def _mapping_all(rows: list[dict]) -> MagicMock:
    m = MagicMock()
    m.mappings.return_value.all.return_value = rows
    return m


def _execute_params(session: MagicMock) -> list[dict]:
    return [call.args[1] for call in session.execute.call_args_list]


def _update_execute_params(session: MagicMock) -> list[dict]:
    out: list[dict] = []
    for call in session.execute.call_args_list:
        params = call.args[1]
        if isinstance(params, dict) and "is_duplicate" in params:
            out.append(params)
    return out


def _execute_sql(session: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in session.execute.call_args_list]


def _promotion_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "quality_score": 0.84,
        "quality_components": {"description": 0.9},
        "field_confidence": {"spam_score": 0.8},
        "overall_confidence": 0.82,
        "spam_tier": "clean",
        "spam_score": 0.25,
    }
    payload.update(overrides)
    return payload


def test_apply_fuzzy_dedup_result_clears_current_row_for_unique_posting() -> None:
    session = MagicMock()
    session.execute.side_effect = [
        _mapping_first({"is_duplicate": False, "duplicate_cluster_id": None}),
        MagicMock(),
    ]
    result = FuzzyDedupResult(
        is_duplicate=False,
        duplicate_cluster_id=None,
        matched_job_posting_id=None,
        survivor_job_posting_id=None,
        stub=False,
    )

    applied = apply_fuzzy_dedup_result(session, CURRENT_ID, result)

    assert applied is True
    assert session.execute.call_count == 2
    assert _update_execute_params(session) == [
        {
            "job_posting_id": CURRENT_ID,
            "is_duplicate": False,
            "duplicate_cluster_id": None,
        }
    ]
    assert "duplicate_cluster_id" in _execute_sql(session)[0]


def test_apply_fuzzy_dedup_result_clears_old_cluster_when_prior_survivor_becomes_unique() -> None:
    session = MagicMock()
    peer_id = "00000000-0000-0000-0000-000000000003"
    session.execute.side_effect = [
        _mapping_first({"is_duplicate": False, "duplicate_cluster_id": CLUSTER_ID}),
        MagicMock(),
        _mapping_all([{"job_posting_id": MATCHED_ID}, {"job_posting_id": peer_id}]),
        MagicMock(),
        MagicMock(),
    ]
    result = FuzzyDedupResult(
        is_duplicate=False,
        duplicate_cluster_id=None,
        matched_job_posting_id=None,
        survivor_job_posting_id=None,
        stub=False,
    )

    applied = apply_fuzzy_dedup_result(session, CURRENT_ID, result)

    assert applied is True
    assert _update_execute_params(session) == [
        {
            "job_posting_id": CURRENT_ID,
            "is_duplicate": False,
            "duplicate_cluster_id": None,
        },
        {
            "job_posting_id": MATCHED_ID,
            "is_duplicate": False,
            "duplicate_cluster_id": None,
        },
        {
            "job_posting_id": peer_id,
            "is_duplicate": False,
            "duplicate_cluster_id": None,
        },
    ]


def test_apply_fuzzy_dedup_result_marks_current_row_duplicate_and_keeps_survivor() -> None:
    session = MagicMock()
    result = FuzzyDedupResult(
        is_duplicate=True,
        duplicate_cluster_id=CLUSTER_ID,
        matched_job_posting_id=MATCHED_ID,
        survivor_job_posting_id=MATCHED_ID,
        stub=False,
    )

    applied = apply_fuzzy_dedup_result(session, CURRENT_ID, result)

    assert applied is True
    assert _update_execute_params(session) == [
        {
            "job_posting_id": CURRENT_ID,
            "is_duplicate": True,
            "duplicate_cluster_id": CLUSTER_ID,
        },
        {
            "job_posting_id": MATCHED_ID,
            "is_duplicate": False,
            "duplicate_cluster_id": CLUSTER_ID,
        },
    ]


def test_apply_fuzzy_dedup_result_flips_prior_survivor_when_current_row_wins() -> None:
    session = MagicMock()
    result = FuzzyDedupResult(
        is_duplicate=False,
        duplicate_cluster_id=CLUSTER_ID,
        matched_job_posting_id=MATCHED_ID,
        survivor_job_posting_id=CURRENT_ID,
        stub=False,
    )

    applied = apply_fuzzy_dedup_result(session, CURRENT_ID, result)

    assert applied is True
    assert _update_execute_params(session) == [
        {
            "job_posting_id": CURRENT_ID,
            "is_duplicate": False,
            "duplicate_cluster_id": CLUSTER_ID,
        },
        {
            "job_posting_id": MATCHED_ID,
            "is_duplicate": True,
            "duplicate_cluster_id": CLUSTER_ID,
        },
    ]


def test_apply_fuzzy_dedup_result_skips_stub_results() -> None:
    session = MagicMock()
    result = FuzzyDedupResult(
        is_duplicate=False,
        duplicate_cluster_id=None,
        matched_job_posting_id=None,
        survivor_job_posting_id=None,
        stub=True,
    )

    applied = apply_fuzzy_dedup_result(session, CURRENT_ID, result)

    assert applied is False
    session.execute.assert_not_called()


def test_apply_fuzzy_dedup_result_rejects_invalid_duplicate_contract() -> None:
    session = MagicMock()
    result = FuzzyDedupResult(
        is_duplicate=True,
        duplicate_cluster_id=None,
        matched_job_posting_id=None,
        survivor_job_posting_id=None,
        stub=False,
    )

    with pytest.raises(ValueError, match="duplicate fuzzy dedup results must include duplicate_cluster_id"):
        apply_fuzzy_dedup_result(session, CURRENT_ID, result)


@pytest.mark.parametrize(
    ("tier", "spam_score"),
    [
        ("clean", 0.25),
        ("flagged", 0.8),
        ("uncertain", None),
    ],
)
def test_apply_enrichment_to_job_postings_calls_dedup_for_non_rejected_tiers(
    tier: str,
    spam_score: float | None,
) -> None:
    session = MagicMock()
    dedup_result = FuzzyDedupResult(
        is_duplicate=False,
        duplicate_cluster_id=None,
        matched_job_posting_id=None,
        survivor_job_posting_id=None,
        stub=False,
    )

    with (
        patch(
            "enrichment.job_postings_promotion.resolve_job_posting_row",
            return_value={"job_posting_id": CURRENT_ID, "company_id": MATCHED_ID},
        ),
        patch("enrichment.job_postings_promotion.run_fuzzy_dedup", return_value=dedup_result) as mock_run,
        patch("enrichment.job_postings_promotion.apply_fuzzy_dedup_result", return_value=True) as mock_apply,
        patch("enrichment.job_postings_promotion.resolve_sector", return_value=None),
    ):
        applied = apply_enrichment_to_job_postings(
            session,
            normalized_job_id=123,
            record_enriched_payload=_promotion_payload(spam_tier=tier, spam_score=spam_score),
        )

    assert applied is True
    mock_run.assert_called_once_with(session, CURRENT_ID)
    mock_apply.assert_called_once_with(session, CURRENT_ID, dedup_result)


def test_apply_enrichment_to_job_postings_skips_dedup_for_rejected_tier() -> None:
    session = MagicMock()

    with (
        patch(
            "enrichment.job_postings_promotion.resolve_job_posting_row",
            return_value={"job_posting_id": CURRENT_ID, "company_id": MATCHED_ID},
        ),
        patch("enrichment.job_postings_promotion.run_fuzzy_dedup") as mock_run,
    ):
        applied = apply_enrichment_to_job_postings(
            session,
            normalized_job_id=123,
            record_enriched_payload=_promotion_payload(spam_tier="rejected", spam_score=0.95),
        )

    assert applied is False
    mock_run.assert_not_called()


def test_apply_enrichment_to_job_postings_logs_and_continues_on_dedup_failure() -> None:
    session = MagicMock()
    session.begin_nested.return_value = nullcontext()

    with (
        patch(
            "enrichment.job_postings_promotion.resolve_job_posting_row",
            return_value={"job_posting_id": CURRENT_ID, "company_id": MATCHED_ID},
        ),
        patch("enrichment.job_postings_promotion.run_fuzzy_dedup", side_effect=RuntimeError("boom")),
        patch("enrichment.job_postings_promotion.resolve_sector", return_value=None),
    ):
        applied = apply_enrichment_to_job_postings(
            session,
            normalized_job_id=123,
            record_enriched_payload=_promotion_payload(),
        )

    assert applied is True
    session.begin_nested.assert_called_once_with()
    assert session.execute.call_count == 1


def _apply_with_resolved_row_for_temporal_borderplex(resolved_row: dict[str, object]) -> tuple[bool, MagicMock]:
    """Promotion UPDATE + patched fuzzy dedup (no extra SQL from dedup path)."""
    session = MagicMock()
    session.begin_nested.return_value = nullcontext()
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = resolved_row
    update_result = MagicMock()
    session.execute.side_effect = [resolve_result, update_result]
    dedup_result = FuzzyDedupResult(
        is_duplicate=False,
        duplicate_cluster_id=None,
        matched_job_posting_id=None,
        survivor_job_posting_id=None,
        stub=False,
    )
    with (
        patch("enrichment.job_postings_promotion.run_fuzzy_dedup", return_value=dedup_result),
        patch("enrichment.job_postings_promotion.apply_fuzzy_dedup_result", return_value=True),
        patch("enrichment.job_postings_promotion.resolve_sector", return_value=None),
    ):
        out = apply_enrichment_to_job_postings(
            session,
            42,
            {
                "spam_tier": "clean",
                "spam_score": 0.2,
                "quality_score": 0.85,
            },
        )
    return out, session


def test_apply_enrichment_binds_temporal_period_from_date_posted() -> None:
    out, session = _apply_with_resolved_row_for_temporal_borderplex(
        {
            "job_posting_id": "11111111-1111-1111-1111-111111111111",
            "company_id": "22222222-2222-2222-2222-222222222222",
            "date_posted": datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        }
    )

    assert out is True
    assert session.execute.call_count == 2
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["temporal_period"] == "post_gpt4"
    assert "soc_code" in params
    assert params["soc_code"] is None
    assert params["naics_code"] == "unknown"


def test_apply_enrichment_binds_temporal_period_at_exact_boundary_date() -> None:
    out, session = _apply_with_resolved_row_for_temporal_borderplex(
        {
            "job_posting_id": "11111111-1111-1111-1111-111111111111",
            "company_id": "22222222-2222-2222-2222-222222222222",
            "date_posted": datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        }
    )

    assert out is True
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["temporal_period"] == "agentic_era"


def test_apply_enrichment_binds_temporal_period_none_when_date_missing() -> None:
    out, session = _apply_with_resolved_row_for_temporal_borderplex(
        {
            "job_posting_id": "11111111-1111-1111-1111-111111111111",
            "company_id": "22222222-2222-2222-2222-222222222222",
            "date_posted": None,
        }
    )

    assert out is True
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["temporal_period"] is None


def test_apply_enrichment_binds_borderplex_subregion_from_normalized_location() -> None:
    out, session = _apply_with_resolved_row_for_temporal_borderplex(
        {
            "job_posting_id": "11111111-1111-1111-1111-111111111111",
            "company_id": "22222222-2222-2222-2222-222222222222",
            "date_posted": datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
            "city": "El Paso",
            "state_province": "Texas",
            "country": "United States",
            "is_remote": False,
            "work_arrangement": "on-site",
        }
    )

    assert out is True
    assert session.execute.call_count == 2
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["borderplex_subregion"] == "el_paso"


def test_apply_enrichment_binds_borderplex_subregion_for_las_cruces() -> None:
    out, session = _apply_with_resolved_row_for_temporal_borderplex(
        {
            "job_posting_id": "11111111-1111-1111-1111-111111111111",
            "company_id": "22222222-2222-2222-2222-222222222222",
            "date_posted": datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
            "city": "Las Cruces",
            "state_province": "New Mexico",
            "country": "United States",
            "is_remote": False,
            "work_arrangement": "on-site",
        }
    )

    assert out is True
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["borderplex_subregion"] == "las_cruces"


def test_apply_enrichment_binds_soc_code_column_from_payload() -> None:
    session = MagicMock()
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = {
        "job_posting_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "date_posted": datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    }
    update_result = MagicMock()
    session.execute.side_effect = [resolve_result, update_result]

    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = apply_enrichment_to_job_postings(
            session,
            42,
            {
                "spam_tier": "clean",
                "spam_score": 0.2,
                "quality_score": 0.85,
                "soc_code": "17-3029",
                "naics_code": "541512",
            },
        )
    assert out is True
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["soc_code"] == "17-3029"
    assert params["naics_code"] == "541512"


def test_apply_enrichment_binds_borderplex_subregion_regional_for_remote_job() -> None:
    out, session = _apply_with_resolved_row_for_temporal_borderplex(
        {
            "job_posting_id": "11111111-1111-1111-1111-111111111111",
            "company_id": "22222222-2222-2222-2222-222222222222",
            "date_posted": datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
            "city": None,
            "state_province": "Texas",
            "country": "United States",
            "is_remote": True,
            "work_arrangement": "Remote",
        }
    )

    assert out is True
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["borderplex_subregion"] == "regional"


def test_apply_enrichment_selects_employer_profile_id_when_metadata_present() -> None:
    ep_id = uuid.uuid4()
    session = MagicMock()
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = {
        "job_posting_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "date_posted": datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    }
    select_scalar = MagicMock()
    select_scalar.scalar_one_or_none.return_value = ep_id
    update_result = MagicMock()
    session.execute.side_effect = [
        resolve_result,
        select_scalar,
        update_result,
        *[MagicMock() for _ in range(10)],
    ]

    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = apply_enrichment_to_job_postings(
            session,
            42,
            {
                "spam_tier": "clean",
                "spam_score": 0.2,
                "quality_score": 0.85,
                "employer_metadata": {"company_size": "smb", "is_known_employer": True},
            },
        )

    assert out is True
    # Index 2: main promotion UPDATE (after resolve + employer_profile id lookup).
    _stmt, params = session.execute.call_args_list[2][0]
    assert params["employer_profile_id"] == ep_id


# ---------------------------------------------------------------------------
# sector_id promotion tests
# ---------------------------------------------------------------------------


def test_apply_enrichment_persists_sector_id_for_known_role() -> None:
    """sector_id resolved via resolve_sector must be written to job_postings."""
    sector_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    session = MagicMock()
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = {
        "job_posting_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "date_posted": datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
    }
    update_result = MagicMock()

    with patch(
        "enrichment.job_postings_promotion.resolve_sector",
        return_value=sector_uuid,
    ) as mock_resolve_sector:
        # Two execute calls: resolve_job_posting_row + UPDATE
        session.execute.side_effect = [resolve_result, update_result]
        out = apply_enrichment_to_job_postings(
            session,
            42,
            {
                "spam_tier": "clean",
                "spam_score": 0.2,
                "quality_score": 0.85,
                "role_classification": "Software Engineering",
            },
        )

    assert out is True
    mock_resolve_sector.assert_called_once_with("Software Engineering", session)
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["sector_id"] == sector_uuid


def test_apply_enrichment_persists_sector_id_for_unknown_role_fallback() -> None:
    """When role_classification is absent, resolve_sector(None) fallback must still be called."""
    other_sector_uuid = "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb"
    session = MagicMock()
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = {
        "job_posting_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "date_posted": None,
    }
    update_result = MagicMock()

    with patch(
        "enrichment.job_postings_promotion.resolve_sector",
        return_value=other_sector_uuid,
    ) as mock_resolve_sector:
        session.execute.side_effect = [resolve_result, update_result]
        out = apply_enrichment_to_job_postings(
            session,
            42,
            {
                "spam_tier": "clean",
                "spam_score": 0.2,
                "quality_score": 0.85,
            },
        )

    assert out is True
    mock_resolve_sector.assert_called_once_with(None, session)
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["sector_id"] == other_sector_uuid
    assert params["sector_id"] is not None


def test_apply_enrichment_sector_id_is_in_params_base_for_all_tiers() -> None:
    """sector_id must appear in the UPDATE params for clean, flagged, and uncertain tiers."""
    sector_uuid = "11112222-3333-4444-5555-666677778888"

    for tier, spam_score in [("clean", 0.2), ("flagged", 0.75), ("uncertain", None)]:
        session = MagicMock()
        resolve_result = MagicMock()
        resolve_result.mappings.return_value.first.return_value = {
            "job_posting_id": "11111111-1111-1111-1111-111111111111",
            "company_id": "22222222-2222-2222-2222-222222222222",
            "date_posted": None,
        }
        update_result = MagicMock()

        with patch(
            "enrichment.job_postings_promotion.resolve_sector",
            return_value=sector_uuid,
        ):
            session.execute.side_effect = [resolve_result, update_result]
            payload: dict[str, object] = {
                "spam_tier": tier,
                "quality_score": 0.85,
                "role_classification": "Data Engineering",
            }
            if spam_score is not None:
                payload["spam_score"] = spam_score

            out = apply_enrichment_to_job_postings(session, 42, payload)

        assert out is True, f"Expected True for tier={tier}"
        _stmt, params = session.execute.call_args_list[1][0]
        assert params.get("sector_id") == sector_uuid, f"sector_id missing or wrong for tier={tier}"


# ---------------------------------------------------------------------------
# Week 8 (#170) — promoted Q&A-ready fields: date_posted, seniority_level, is_remote
# ---------------------------------------------------------------------------


def _resolved_row_with_qna_fields(
    *,
    date_posted: object = None,
    is_remote: object = None,
) -> dict[str, object]:
    return {
        "job_posting_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "date_posted": date_posted,
        "is_remote": is_remote,
    }


def _apply_with_qna_payload(session: MagicMock, resolved_row: dict, payload_overrides: dict) -> dict:
    """Run apply_enrichment_to_job_postings; return the UPDATE params dict."""
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = resolved_row
    update_result = MagicMock()
    session.execute.side_effect = [resolve_result, update_result]

    payload: dict[str, object] = {
        "spam_tier": "clean",
        "spam_score": 0.2,
        "quality_score": 0.85,
        **payload_overrides,
    }
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = apply_enrichment_to_job_postings(session, 42, payload)

    assert out is True
    _stmt, params = session.execute.call_args_list[1][0]
    return params


def test_apply_enrichment_binds_date_posted_from_resolved_row() -> None:
    """date_posted from normalized_jobs resolved row must be bound in the UPDATE params."""
    posted_at = datetime(2024, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(date_posted=posted_at),
        {},
    )
    assert params["date_posted"] == posted_at


def test_apply_enrichment_binds_date_posted_none_when_missing_from_resolved_row() -> None:
    """date_posted must be None (not absent) when the resolved row has no date."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(date_posted=None),
        {},
    )
    assert "date_posted" in params
    assert params["date_posted"] is None


def test_apply_enrichment_binds_seniority_level_from_seniority_key() -> None:
    """seniority_level param must be populated from the 'seniority' payload key (RecordEnriched contract)."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(),
        {"seniority": "Senior"},
    )
    assert params["seniority_level"] == "Senior"


def test_apply_enrichment_binds_seniority_level_from_seniority_level_key() -> None:
    """'seniority_level' key takes precedence over 'seniority' when both are present."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(),
        {"seniority": "Mid-level", "seniority_level": "Senior"},
    )
    assert params["seniority_level"] == "Senior"


def test_apply_enrichment_binds_seniority_level_none_when_absent() -> None:
    """seniority_level param must be None when no seniority key appears in the payload."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(),
        {},
    )
    assert "seniority_level" in params
    assert params["seniority_level"] is None


def test_apply_enrichment_binds_seniority_level_strips_whitespace() -> None:
    """Whitespace-only seniority values must be treated as None, not stored."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(),
        {"seniority": "   "},
    )
    assert params["seniority_level"] is None


def test_apply_enrichment_binds_is_remote_true_from_resolved_row() -> None:
    """is_remote=True in the resolved row must be bound as Python True in UPDATE params."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(is_remote=True),
        {},
    )
    assert params["is_remote"] is True


def test_apply_enrichment_binds_is_remote_false_from_resolved_row() -> None:
    """is_remote=False in the resolved row must be bound as Python False in UPDATE params."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(is_remote=False),
        {},
    )
    assert params["is_remote"] is False


def test_apply_enrichment_binds_is_remote_none_when_missing() -> None:
    """is_remote must be None (not absent) when the resolved row has no value."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(is_remote=None),
        {},
    )
    assert "is_remote" in params
    assert params["is_remote"] is None


def test_apply_enrichment_coerces_is_remote_int_to_bool() -> None:
    """DB-returned integer truthy values (e.g. 1) must be coerced to Python bool."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(is_remote=1),
        {},
    )
    assert params["is_remote"] is True
    assert isinstance(params["is_remote"], bool)


@pytest.mark.parametrize("tier,spam_score", [("clean", 0.2), ("flagged", 0.75), ("uncertain", None)])
def test_apply_enrichment_qna_fields_present_for_all_tiers(tier: str, spam_score: float | None) -> None:
    """date_posted, seniority_level, and is_remote must be bound for every spam tier."""
    posted_at = datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)
    session = MagicMock()
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = {
        "job_posting_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "date_posted": posted_at,
        "is_remote": True,
    }
    update_result = MagicMock()
    session.execute.side_effect = [resolve_result, update_result]

    payload: dict[str, object] = {
        "spam_tier": tier,
        "quality_score": 0.85,
        "seniority": "Entry-level",
    }
    if spam_score is not None:
        payload["spam_score"] = spam_score

    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = apply_enrichment_to_job_postings(session, 42, payload)

    assert out is True, f"Expected True for tier={tier!r}"
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["date_posted"] == posted_at, f"date_posted wrong for tier={tier!r}"
    assert params["seniority_level"] == "Entry-level", f"seniority_level wrong for tier={tier!r}"
    assert params["is_remote"] is True, f"is_remote wrong for tier={tier!r}"
