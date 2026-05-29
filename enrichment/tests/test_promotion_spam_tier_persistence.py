"""Tests for JIE #308 Fix B-1 + Fix A — `spam_tier` persistence on every promotion path.

Pre-#308, promotion UPDATEs did not persist ``dbo.job_postings.spam_tier``. Post-#308,
``apply_enrichment_to_job_postings`` runs a single ``_UPDATE_COMMON_SQL`` statement
and passes ``is_spam``, ``spam_score``, and ``spam_tier`` as bound parameters so
each tier (clean / flagged / uncertain) persists the correct values without
duplicating the full UPDATE body.

Plus Fix A — the previously-WARNING ``enrichment_promotion_unhandled_tier`` log
is now ERROR-level so future regressions surface in PR-CI rather than buried
in WARNING dashboards.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from enrichment.job_postings_promotion import (
    _UPDATE_COMMON_SQL,
    apply_enrichment_to_job_postings,
)
from enrichment.schemas import RecordEnrichedPayload


def _mapping_first(row: dict | None) -> MagicMock:
    m = MagicMock()
    m.mappings.return_value.first.return_value = row
    return m


def _mapping_all(rows: list[dict]) -> MagicMock:
    m = MagicMock()
    m.mappings.return_value.all.return_value = rows
    return m


def _resolved_row() -> dict:
    return {
        "job_posting_id": "00000000-0000-0000-0000-000000000001",
        "company_id": "00000000-0000-0000-0000-0000000000aa",
        "date_posted": None,
        "is_remote": False,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "zip_code": None,
    }


def _payload(*, tier: str | None, score: float | None) -> RecordEnrichedPayload:
    return RecordEnrichedPayload.model_validate(
        {
            "quality_score": 0.84,
            "field_confidence": {"spam_score": 0.8},
            "overall_confidence": 0.82,
            "spam_tier": tier,
            "spam_score": score,
            "naics_code": "541110",
        }
    )


def _build_session() -> MagicMock:
    """Mock session whose execute() handles the resolve / sector / employer / update / dedup calls.

    The promotion path issues several executes in sequence (resolve, employer-id,
    sector resolve, the tier UPDATE, the dedup load + UPDATE, and the
    ``promoted_at`` stamp from PR #301). The first ``mappings().first()`` returns
    the resolved row; the others return generic MagicMocks so any ``.scalar_one_or_none()``
    or follow-on calls succeed without raising.
    """
    session = MagicMock()
    session.execute.side_effect = [
        _mapping_first(_resolved_row()),  # resolve_job_posting_row
        *[MagicMock() for _ in range(20)],  # remaining executes (sector, employer, UPDATE, dedup, stamp)
    ]
    return session


def _params_for_sql(session: MagicMock, sql_obj) -> dict:
    """Return the params dict from the execute() call that ran ``sql_obj``."""
    expected = str(sql_obj)
    for call in session.execute.call_args_list:
        if str(call.args[0]) == expected:
            return call.args[1]
    raise AssertionError(f"No execute() call matched SQL\n{expected}")


def test_common_promotion_sql_binds_spam_columns() -> None:
    """JIE #308 Fix B-1: unified UPDATE binds is_spam, spam_score, and spam_tier."""
    sql = str(_UPDATE_COMMON_SQL)
    assert "is_spam = :is_spam" in sql
    assert "spam_score = :spam_score" in sql
    assert "spam_tier = :spam_tier" in sql


@pytest.mark.parametrize(
    ("tier", "score", "expected_spam_tier", "expected_is_spam"),
    [
        ("clean", 0.2, "clean", False),
        ("flagged", 0.8, "flagged", None),
        ("uncertain", None, "uncertain", None),
    ],
)
def test_apply_enrichment_dispatches_spam_params_per_tier(
    tier: str,
    score: float | None,
    expected_spam_tier: str,
    expected_is_spam: bool | None,
) -> None:
    """Each tier routes through ``_UPDATE_COMMON_SQL`` with the correct spam binds."""
    session = _build_session()
    payload = _payload(tier=tier, score=score)

    apply_enrichment_to_job_postings(session, normalized_job_id=42, record_enriched_payload=payload)

    sqls_executed = [str(c.args[0]) for c in session.execute.call_args_list]
    assert str(_UPDATE_COMMON_SQL) in sqls_executed, f"Expected promotion UPDATE for tier={tier}"

    params = _params_for_sql(session, _UPDATE_COMMON_SQL)
    assert params["spam_tier"] == expected_spam_tier
    assert params["is_spam"] is expected_is_spam
    if tier == "uncertain":
        assert params["spam_score"] is None
    else:
        assert params["spam_score"] == score


def test_uncertain_path_binds_spam_score_none() -> None:
    """JIE #308 Fix B-2: uncertain path binds spam_score=None when classifier produced none."""
    session = _build_session()
    payload = _payload(tier="uncertain", score=None)

    apply_enrichment_to_job_postings(session, normalized_job_id=42, record_enriched_payload=payload)

    params = _params_for_sql(session, _UPDATE_COMMON_SQL)
    assert "spam_score" in params, "promotion UPDATE missing spam_score bind"
    assert params["spam_score"] is None
    assert params["spam_tier"] == "uncertain"
    assert params["is_spam"] is None


def test_unhandled_tier_logs_error_not_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """JIE #308 Fix A: an unrecognized tier value must produce an ERROR log, not WARNING.

    Before Fix A this was a warning buried in routine dashboards. Now it's a loud
    ERROR so a future regression introducing a new tier value (typo, case mismatch,
    classifier output drift) shows up in PR-CI noise immediately.

    Invalid tier *strings* are caught at the Pydantic boundary
    (``RecordEnrichedPayload.spam_tier`` is a ``Literal``). The structurally-reachable
    way to exercise the promotion function's defensive fall-through is for
    ``apply_spam_tiers`` to return a tier outside the four-state model at runtime.
    We simulate that via monkeypatch; the payload carries ``spam_tier=None`` so that
    ``_coerce_enrichment_params`` falls through to the ``apply_spam_tiers`` derivation
    path and picks up the injected unknown tier.
    """
    import enrichment.job_postings_promotion as promotion_mod

    monkeypatch.setattr(promotion_mod, "apply_spam_tiers", lambda *args, **kwargs: (None, "future_unknown_tier"))

    # Capture log calls directly — structlog's processors don't always feed
    # caplog cleanly, so we observe the bound logger's error/warning methods.
    error_calls: list[tuple] = []
    warning_calls: list[tuple] = []
    monkeypatch.setattr(promotion_mod.log, "error", lambda *a, **kw: error_calls.append((a, kw)))
    monkeypatch.setattr(promotion_mod.log, "warning", lambda *a, **kw: warning_calls.append((a, kw)))

    session = _build_session()
    # spam_tier=None: Pydantic accepts it; _coerce_enrichment_params falls through
    # to apply_spam_tiers (monkeypatched) which injects the unknown tier string.
    payload = _payload(tier=None, score=0.5)

    applied = apply_enrichment_to_job_postings(session, normalized_job_id=42, record_enriched_payload=payload)

    assert applied is False, "Unhandled tier must return False (don't pretend success)"
    # Find the unhandled-tier error call — must be ERROR, not WARNING.
    unhandled_err = [c for c in error_calls if c[0] and "unhandled_tier" in c[0][0]]
    unhandled_warn = [c for c in warning_calls if c[0] and "unhandled_tier" in c[0][0]]
    assert unhandled_err, (
        f"Expected log.error('enrichment_promotion_unhandled_tier', ...). "
        f"Got error_calls={error_calls!r}, warning_calls={warning_calls!r}"
    )
    assert not unhandled_warn, "Fix A regression: unhandled_tier dropped back to WARNING"
    # Verify the structured fields the guard surfaces (helps debugging future regressions).
    _, kwargs = unhandled_err[0]
    assert kwargs.get("tier_resolved") == "future_unknown_tier"
    assert kwargs.get("tier_raw") is None  # payload carried spam_tier=None; derivation injected the unknown tier
    assert kwargs.get("spam_score_payload") == 0.5
