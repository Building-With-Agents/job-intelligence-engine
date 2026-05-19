"""Unit tests for enrichment promotion helpers."""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

from enrichment.dedup.types import FuzzyDedupResult
from enrichment.job_postings_promotion import (
    _coerce_enrichment_params,
    _PromotionCoercionReady,
    _PromotionCoercionSkip,
    apply_enrichment_to_job_postings,
    apply_fuzzy_dedup_result,
)
from enrichment.schemas import RecordEnrichedPayload

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


def _promotion_payload(**overrides: object) -> RecordEnrichedPayload:
    payload: dict[str, object] = {
        "quality_score": 0.84,
        "quality_components": {"description": 0.9},
        "field_confidence": {"spam_score": 0.8},
        "overall_confidence": 0.82,
        "spam_tier": "clean",
        "spam_score": 0.25,
    }
    payload.update(overrides)
    return RecordEnrichedPayload.model_validate(payload)


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
    # JIE #289: 2 calls now — the original promotion UPDATE plus the
    # promoted_at timestamp stamp on dbo.normalized_jobs that runs after
    # fuzzy dedup (regardless of dedup success/failure) so the row is
    # observable to scripts/sweep_unpromoted_normalized_jobs.py.
    assert session.execute.call_count == 2


def _apply_with_resolved_row_for_temporal_borderplex(resolved_row: dict[str, object]) -> tuple[bool, MagicMock]:
    """Promotion UPDATE + promoted_at stamp (#289) + patched fuzzy dedup (no extra SQL from dedup path)."""
    session = MagicMock()
    session.begin_nested.return_value = nullcontext()
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = resolved_row
    update_result = MagicMock()
    promoted_at_stamp_result = MagicMock()
    session.execute.side_effect = [resolve_result, update_result, promoted_at_stamp_result]
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
            RecordEnrichedPayload.model_validate(
                {
                    "spam_tier": "clean",
                    "spam_score": 0.2,
                    "quality_score": 0.85,
                }
            ),
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
    # JIE #289: 3 calls now — resolve + UPDATE + promoted_at stamp.
    assert session.execute.call_count == 3
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
    # JIE #289: 3 calls now — resolve + UPDATE + promoted_at stamp.
    assert session.execute.call_count == 3
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
    # JIE #289: extra mocks cover any session.execute() calls inside the
    # un-patched fuzzy dedup path plus the new promoted_at stamp.
    session.execute.side_effect = [
        resolve_result,
        update_result,
        *[MagicMock() for _ in range(10)],
    ]

    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = apply_enrichment_to_job_postings(
            session,
            42,
            RecordEnrichedPayload.model_validate(
                {
                    "spam_tier": "clean",
                    "spam_score": 0.2,
                    "quality_score": 0.85,
                    "soc_code": "17-3029",
                    "naics_code": "541512",
                }
            ),
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
            RecordEnrichedPayload.model_validate(
                {
                    "spam_tier": "clean",
                    "spam_score": 0.2,
                    "quality_score": 0.85,
                    "employer_metadata": {"company_size": "smb", "is_known_employer": True},
                }
            ),
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
        # JIE #289: extra mocks cover any session.execute() calls inside the
        # un-patched fuzzy dedup path plus the new promoted_at stamp.
        session.execute.side_effect = [
            resolve_result,
            update_result,
            *[MagicMock() for _ in range(10)],
        ]
        out = apply_enrichment_to_job_postings(
            session,
            42,
            RecordEnrichedPayload.model_validate(
                {
                    "spam_tier": "clean",
                    "spam_score": 0.2,
                    "quality_score": 0.85,
                    "role_classification": "Software Engineering",
                }
            ),
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
        # JIE #289: extra mocks cover any session.execute() calls inside the
        # un-patched fuzzy dedup path plus the new promoted_at stamp.
        session.execute.side_effect = [
            resolve_result,
            update_result,
            *[MagicMock() for _ in range(10)],
        ]
        out = apply_enrichment_to_job_postings(
            session,
            42,
            RecordEnrichedPayload.model_validate(
                {
                    "spam_tier": "clean",
                    "spam_score": 0.2,
                    "quality_score": 0.85,
                }
            ),
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
            # JIE #289: extra mocks cover any session.execute() calls inside the
            # un-patched fuzzy dedup path plus the new promoted_at stamp.
            session.execute.side_effect = [
                resolve_result,
                update_result,
                *[MagicMock() for _ in range(10)],
            ]
            payload: dict[str, object] = {
                "spam_tier": tier,
                "quality_score": 0.85,
                "role_classification": "Data Engineering",
            }
            if spam_score is not None:
                payload["spam_score"] = spam_score

            out = apply_enrichment_to_job_postings(
                session,
                42,
                RecordEnrichedPayload.model_validate(payload),
            )

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
    salary_min: object = None,
    salary_max: object = None,
    salary_currency: object = None,
    salary_period: object = None,
) -> dict[str, object]:
    return {
        "job_posting_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "date_posted": date_posted,
        "is_remote": is_remote,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "salary_period": salary_period,
    }


def _apply_with_qna_payload(session: MagicMock, resolved_row: dict, payload_overrides: dict) -> dict:
    """Run apply_enrichment_to_job_postings; return the UPDATE params dict."""
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = resolved_row
    update_result = MagicMock()
    # JIE #289: extra mocks cover any session.execute() calls inside the
    # un-patched fuzzy dedup path plus the new promoted_at stamp on
    # dbo.normalized_jobs that runs at the end of _finish_with_dedup.
    session.execute.side_effect = [
        resolve_result,
        update_result,
        *[MagicMock() for _ in range(10)],
    ]

    payload: dict[str, object] = {
        "spam_tier": "clean",
        "spam_score": 0.2,
        "quality_score": 0.85,
        **payload_overrides,
    }
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = apply_enrichment_to_job_postings(
            session,
            42,
            RecordEnrichedPayload.model_validate(payload),
        )

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


def test_apply_enrichment_derives_quality_when_payload_omits_score_jsearch_null_date() -> None:
    """JIE #328: NULL date_posted must not block promotion; missing quality_score is derived from nj+EI."""
    session = MagicMock()
    resolve_result = MagicMock()
    resolve_result.mappings.return_value.first.return_value = _resolved_row_with_qna_fields(date_posted=None)
    ei_result = MagicMock()
    ei_result.mappings.return_value.first.return_value = {
        "title": "Warehouse Supervisor",
        "description": ("Lead daily operations. " * 80)
        + "\n\nResponsibilities:\n- Staffing\n- Safety compliance\n- KPI reporting",
        "skills": [{"skill_name": "Leadership", "type": "Soft"}],
        "tools": [],
        "tasks": [{"text": "Supervise crew"}],
        "responsibilities": [{"responsibility_description": "Ensure on-time fulfillment"}],
        "context": [],
        "extraction_failed": False,
    }
    update_result = MagicMock()
    session.execute.side_effect = [
        resolve_result,
        ei_result,
        update_result,
        *[MagicMock() for _ in range(10)],
    ]
    payload: dict[str, object] = {
        "spam_tier": "clean",
        "spam_score": 0.2,
    }
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = apply_enrichment_to_job_postings(session, 4242, RecordEnrichedPayload.model_validate(payload))
    assert out is True
    assert session.execute.call_count >= 3
    _stmt, params = session.execute.call_args_list[2][0]
    assert "quality_score" in params
    assert params["date_posted"] is None
    assert 0.0 < float(params["quality_score"]) <= 1.0


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
    # JIE #289: extra mocks cover any session.execute() calls inside the
    # un-patched fuzzy dedup path plus the new promoted_at stamp.
    session.execute.side_effect = [
        resolve_result,
        update_result,
        *[MagicMock() for _ in range(10)],
    ]

    payload: dict[str, object] = {
        "spam_tier": tier,
        "quality_score": 0.85,
        "seniority": "Entry-level",
    }
    if spam_score is not None:
        payload["spam_score"] = spam_score

    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = apply_enrichment_to_job_postings(
            session,
            42,
            RecordEnrichedPayload.model_validate(payload),
        )

    assert out is True, f"Expected True for tier={tier!r}"
    _stmt, params = session.execute.call_args_list[1][0]
    assert params["date_posted"] == posted_at, f"date_posted wrong for tier={tier!r}"
    assert params["seniority_level"] == "Entry-level", f"seniority_level wrong for tier={tier!r}"
    assert params["is_remote"] is True, f"is_remote wrong for tier={tier!r}"


# ---------------------------------------------------------------------------
# Field-level coverage for #173 (role_classification) and #174 (structured salary)
# ---------------------------------------------------------------------------


def test_apply_enrichment_binds_role_classification_from_payload() -> None:
    """role_classification from RecordEnriched payload must be bound in UPDATE params."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(),
        {"role_classification": "Software Engineer"},
    )
    assert params["role_classification"] == "Software Engineer"


def test_apply_enrichment_binds_role_classification_none_when_absent() -> None:
    """role_classification must be None (not absent) when no key in the payload."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(),
        {},
    )
    assert "role_classification" in params
    assert params["role_classification"] is None


def test_apply_enrichment_binds_role_classification_strips_whitespace() -> None:
    """Whitespace-only role_classification values must be treated as None."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(),
        {"role_classification": "   "},
    )
    assert params["role_classification"] is None


def test_apply_enrichment_binds_salary_min_from_resolved_row() -> None:
    """salary_min from normalized_jobs resolved row must be bound in UPDATE params."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(salary_min=85000.0),
        {},
    )
    assert params["salary_min"] == 85000.0


def test_apply_enrichment_binds_salary_max_from_resolved_row() -> None:
    """salary_max from normalized_jobs resolved row must be bound in UPDATE params."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(salary_max=145000.0),
        {},
    )
    assert params["salary_max"] == 145000.0


def test_apply_enrichment_binds_salary_currency_from_resolved_row() -> None:
    """salary_currency from normalized_jobs resolved row must be bound in UPDATE params."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(salary_currency="USD"),
        {},
    )
    assert params["salary_currency"] == "USD"


def test_apply_enrichment_binds_salary_period_from_resolved_row() -> None:
    """salary_period from normalized_jobs resolved row must be bound in UPDATE params."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(salary_period="annual"),
        {},
    )
    assert params["salary_period"] == "annual"


def test_apply_enrichment_binds_salary_columns_none_when_missing() -> None:
    """All 4 structured salary params must be None (not absent) when the resolved row has nulls."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(),
        {},
    )
    for key in ("salary_min", "salary_max", "salary_currency", "salary_period"):
        assert key in params
        assert params[key] is None, f"{key} must be None when missing from resolved row"


def test_apply_enrichment_binds_salary_currency_strips_whitespace() -> None:
    """Whitespace-only salary_currency must be treated as None."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(salary_currency="  "),
        {},
    )
    assert params["salary_currency"] is None


def test_apply_enrichment_binds_salary_period_strips_whitespace() -> None:
    """Whitespace-only salary_period must be treated as None."""
    session = MagicMock()
    params = _apply_with_qna_payload(
        session,
        _resolved_row_with_qna_fields(salary_period="   "),
        {},
    )
    assert params["salary_period"] is None


# ---------------------------------------------------------------------------
# _coerce_enrichment_params (Pair C P1) — isolated coercion / early-exit
# ---------------------------------------------------------------------------


def _coerce_resolved(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "job_posting_id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "date_posted": None,
        "city": None,
        "state_province": None,
        "country": None,
        "is_remote": None,
        "work_arrangement": None,
        "zip_code": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
    }
    row.update(overrides)
    return row


def test_coerce_enrichment_params_rejected_spam() -> None:
    session = MagicMock()
    out = _coerce_enrichment_params(
        session,
        1,
        {"spam_tier": "rejected", "spam_score": 0.95, "quality_score": 0.8},
        _coerce_resolved(),
    )
    assert isinstance(out, _PromotionCoercionSkip)
    assert out.reason == "rejected_spam"
    session.execute.assert_not_called()


def test_coerce_enrichment_params_no_quality_after_derivation_none() -> None:
    session = MagicMock()
    with patch(
        "enrichment.job_postings_promotion._derive_quality_from_normalized_job",
        return_value=None,
    ):
        out = _coerce_enrichment_params(
            session,
            42,
            {"spam_tier": "clean", "spam_score": 0.1},
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionSkip)
    assert out.reason == "no_quality_score"


def test_coerce_enrichment_params_invalid_quality_score() -> None:
    session = MagicMock()
    out = _coerce_enrichment_params(
        session,
        1,
        {"spam_tier": "clean", "spam_score": 0.1, "quality_score": "not-a-float"},
        _coerce_resolved(),
    )
    assert isinstance(out, _PromotionCoercionSkip)
    assert out.reason == "invalid_quality_score"


def test_coerce_enrichment_params_quality_derivation_happy_path_sets_score_and_field_confidence_json() -> None:
    session = MagicMock()
    with (
        patch("enrichment.job_postings_promotion.resolve_sector", return_value=None),
        patch(
            "enrichment.job_postings_promotion._derive_quality_from_normalized_job",
            return_value=(0.75, {"completeness": 0.8}),
        ),
    ):
        out = _coerce_enrichment_params(
            session,
            99,
            {"spam_tier": "clean", "spam_score": 0.1},
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["quality_score"] == 0.75
    fc = out.params_base["field_confidence"]
    assert isinstance(fc, str) and fc
    parsed = json.loads(fc)
    assert isinstance(parsed, dict)


def test_coerce_enrichment_params_derives_tier_from_spam_score_when_tier_unset() -> None:
    session = MagicMock()
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {"spam_score": 0.25, "quality_score": 0.9},
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.tier == "clean"
    assert out.params_base["quality_score"] == 0.9


def test_coerce_enrichment_params_naics_unknown_when_missing() -> None:
    session = MagicMock()
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {"spam_tier": "clean", "spam_score": 0.1, "quality_score": 0.9},
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["naics_code"] == "unknown"


def test_coerce_enrichment_params_naics_strips_nonempty() -> None:
    session = MagicMock()
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {
                "spam_tier": "clean",
                "spam_score": 0.1,
                "quality_score": 0.9,
                "naics_code": "  541512  ",
            },
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["naics_code"] == "541512"


def test_coerce_enrichment_params_soc_none_when_blank() -> None:
    session = MagicMock()
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {"spam_tier": "clean", "spam_score": 0.1, "quality_score": 0.9, "soc_code": "   "},
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["soc_code"] is None


def test_coerce_enrichment_params_soc_persisted_when_present() -> None:
    session = MagicMock()
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {
                "spam_tier": "clean",
                "spam_score": 0.1,
                "quality_score": 0.9,
                "soc_code": "15-1252.00",
            },
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["soc_code"] == "15-1252.00"


def test_coerce_enrichment_params_seniority_level_over_seniority() -> None:
    session = MagicMock()
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {
                "spam_tier": "clean",
                "spam_score": 0.1,
                "quality_score": 0.9,
                "seniority": "Mid",
                "seniority_level": "Senior",
            },
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["seniority_level"] == "Senior"


def test_coerce_enrichment_params_is_remote_int_coerced_to_bool() -> None:
    session = MagicMock()
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {"spam_tier": "clean", "spam_score": 0.1, "quality_score": 0.9},
            _coerce_resolved(is_remote=1),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["is_remote"] is True
    assert isinstance(out.params_base["is_remote"], bool)


def test_coerce_enrichment_params_employer_metadata_non_dict_skips_profile_sql() -> None:
    session = MagicMock()
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {
                "spam_tier": "clean",
                "spam_score": 0.1,
                "quality_score": 0.9,
                "employer_metadata": "not-a-dict",
            },
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["employer_profile_id"] is None
    session.execute.assert_not_called()


def test_coerce_enrichment_params_employer_metadata_select_then_upsert() -> None:
    session = MagicMock()
    ep_uuid = uuid.uuid4()
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = None
    session.execute.return_value = select_result
    with (
        patch("enrichment.job_postings_promotion.resolve_sector", return_value=None),
        patch(
            "enrichment.job_postings_promotion.upsert_employer_profile_by_company_id",
            return_value=ep_uuid,
        ) as mock_upsert,
    ):
        out = _coerce_enrichment_params(
            session,
            1,
            {
                "spam_tier": "clean",
                "spam_score": 0.1,
                "quality_score": 0.9,
                "employer_metadata": {"company_size": "smb"},
            },
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["employer_profile_id"] == ep_uuid
    mock_upsert.assert_called_once()
    session.execute.assert_called_once()


def test_coerce_enrichment_params_uses_existing_employer_profile_id() -> None:
    session = MagicMock()
    ep_uuid = uuid.uuid4()
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = ep_uuid
    session.execute.return_value = select_result
    with patch("enrichment.job_postings_promotion.resolve_sector", return_value=None):
        out = _coerce_enrichment_params(
            session,
            1,
            {
                "spam_tier": "clean",
                "spam_score": 0.1,
                "quality_score": 0.9,
                "employer_metadata": {"company_size": "smb"},
            },
            _coerce_resolved(),
        )
    assert isinstance(out, _PromotionCoercionReady)
    assert out.params_base["employer_profile_id"] == ep_uuid
    session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# P2 — fuzzy_dedup_contract_violation metric counter (JIE phase-1-cleanup-pair-c)
# ---------------------------------------------------------------------------


def test_apply_fuzzy_dedup_after_promotion_logs_contract_violation_for_value_error() -> None:
    """ValueError from apply_fuzzy_dedup_result must log fuzzy_dedup_contract_violation,
    not fuzzy_dedup_after_promotion_failed, so dashboards count contract violations
    as a distinct metric from infrastructure failures."""
    session = MagicMock()
    session.begin_nested.return_value = nullcontext()

    with (
        patch(
            "enrichment.job_postings_promotion.resolve_job_posting_row",
            return_value={"job_posting_id": CURRENT_ID, "company_id": MATCHED_ID},
        ),
        patch(
            "enrichment.job_postings_promotion.run_fuzzy_dedup",
            return_value=FuzzyDedupResult(
                is_duplicate=False,
                duplicate_cluster_id=None,
                matched_job_posting_id=None,
                survivor_job_posting_id=None,
                stub=False,
            ),
        ),
        patch(
            "enrichment.job_postings_promotion.apply_fuzzy_dedup_result",
            side_effect=ValueError("duplicate fuzzy dedup results must include duplicate_cluster_id"),
        ),
        patch("enrichment.job_postings_promotion.resolve_sector", return_value=None),
        structlog.testing.capture_logs() as cap_logs,
    ):
        applied = apply_enrichment_to_job_postings(
            session,
            normalized_job_id=123,
            record_enriched_payload=_promotion_payload(),
        )

    assert applied is True
    violation_events = [e for e in cap_logs if e.get("event") == "fuzzy_dedup_contract_violation"]
    assert len(violation_events) == 1, f"expected 1 contract violation log, got: {cap_logs}"
    assert violation_events[0]["job_posting_id"] == CURRENT_ID
    assert violation_events[0]["normalized_job_id"] == 123
    assert "duplicate_cluster_id" in violation_events[0]["error"]
    # Must NOT fall through to the generic infrastructure failure key
    assert not any(e.get("event") == "fuzzy_dedup_after_promotion_failed" for e in cap_logs)


def test_apply_fuzzy_dedup_after_promotion_logs_general_failure_for_non_value_error() -> None:
    """Non-ValueError exceptions (DB errors, network timeouts) must still log
    fuzzy_dedup_after_promotion_failed — the original infrastructure failure key."""
    session = MagicMock()
    session.begin_nested.return_value = nullcontext()

    with (
        patch(
            "enrichment.job_postings_promotion.resolve_job_posting_row",
            return_value={"job_posting_id": CURRENT_ID, "company_id": MATCHED_ID},
        ),
        patch(
            "enrichment.job_postings_promotion.run_fuzzy_dedup",
            side_effect=RuntimeError("db pool exhausted"),
        ),
        patch("enrichment.job_postings_promotion.resolve_sector", return_value=None),
        structlog.testing.capture_logs() as cap_logs,
    ):
        applied = apply_enrichment_to_job_postings(
            session,
            normalized_job_id=456,
            record_enriched_payload=_promotion_payload(),
        )

    assert applied is True
    failure_events = [e for e in cap_logs if e.get("event") == "fuzzy_dedup_after_promotion_failed"]
    assert len(failure_events) == 1, f"expected 1 infrastructure failure log, got: {cap_logs}"
    assert failure_events[0]["job_posting_id"] == CURRENT_ID
    assert failure_events[0]["normalized_job_id"] == 456
    assert "db pool exhausted" in failure_events[0]["error"]
    # Must NOT be misclassified as a contract violation
    assert not any(e.get("event") == "fuzzy_dedup_contract_violation" for e in cap_logs)
