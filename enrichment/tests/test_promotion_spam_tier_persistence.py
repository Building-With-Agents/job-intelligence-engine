"""Tests for JIE #308 Fix B-1 + Fix A — `spam_tier` persistence on every promotion path.

Pre-#308, the three UPDATE SQL constants in ``enrichment/job_postings_promotion.py``
(``_UPDATE_CLEAN_SQL`` / ``_UPDATE_FLAGGED_SQL`` / ``_UPDATE_UNCERTAIN_SQL``) read the
classifier's tier output to pick which UPDATE to run, but none of them actually
persisted the ``spam_tier`` column. So ``dbo.job_postings.spam_tier`` was NULL on
every row, even successfully-classified ones, and there was no way to distinguish
"classifier ran with tier='flagged'" from "classifier never ran."

Post-#308, each UPDATE writes its tier as a hardcoded literal:
- ``_UPDATE_CLEAN_SQL`` writes ``spam_tier = 'clean'``
- ``_UPDATE_FLAGGED_SQL`` writes ``spam_tier = 'flagged'``
- ``_UPDATE_UNCERTAIN_SQL`` writes ``spam_tier = 'uncertain'`` + ``is_spam = NULL`` + ``spam_score = :spam_score``

Plus Fix A — the previously-WARNING ``enrichment_promotion_unhandled_tier`` log
is now ERROR-level so future regressions surface in PR-CI rather than buried
in WARNING dashboards.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from enrichment.job_postings_promotion import (
    _UPDATE_CLEAN_SQL,
    _UPDATE_FLAGGED_SQL,
    _UPDATE_UNCERTAIN_SQL,
    apply_enrichment_to_job_postings,
)
from enrichment.types import RecordEnrichedPayload


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


def _payload(*, tier: str, score: float | None) -> RecordEnrichedPayload:
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


def test_clean_tier_sql_includes_spam_tier_literal() -> None:
    """JIE #308 Fix B-1: _UPDATE_CLEAN_SQL must write spam_tier = 'clean'."""
    sql = str(_UPDATE_CLEAN_SQL)
    assert "spam_tier = 'clean'" in sql, "_UPDATE_CLEAN_SQL missing spam_tier literal"
    assert "is_spam = FALSE" in sql, "Clean tier should still write is_spam = FALSE"


def test_flagged_tier_sql_includes_spam_tier_literal() -> None:
    """JIE #308 Fix B-1: _UPDATE_FLAGGED_SQL must write spam_tier = 'flagged' + keep is_spam = NULL."""
    sql = str(_UPDATE_FLAGGED_SQL)
    assert "spam_tier = 'flagged'" in sql
    # NULL semantics preserved per Gary's HITL rule — flagged rows aren't surfaced
    # in the natural flow until manually approved.
    assert "is_spam = NULL" in sql, "Flagged tier MUST keep is_spam = NULL (HITL queue)"


def test_uncertain_tier_sql_includes_spam_tier_literal_and_is_spam_null() -> None:
    """JIE #308 Fix B-1 + B-2: _UPDATE_UNCERTAIN_SQL writes spam_tier = 'uncertain' + is_spam = NULL + spam_score bind."""
    sql = str(_UPDATE_UNCERTAIN_SQL)
    assert "spam_tier = 'uncertain'" in sql
    assert "is_spam = NULL" in sql
    assert "spam_score = :spam_score" in sql, (
        "Uncertain tier should bind spam_score (None when classifier produced no score)"
    )


@pytest.mark.parametrize(
    ("tier", "score", "expected_sql"),
    [
        ("clean", 0.2, _UPDATE_CLEAN_SQL),
        ("flagged", 0.8, _UPDATE_FLAGGED_SQL),
        ("uncertain", None, _UPDATE_UNCERTAIN_SQL),
    ],
)
def test_apply_enrichment_dispatches_to_correct_tier_update(tier: str, score: float | None, expected_sql) -> None:
    """JIE #308 Fix B-1: each tier value routes to its own UPDATE constant."""
    session = _build_session()
    payload = _payload(tier=tier, score=score)

    apply_enrichment_to_job_postings(session, normalized_job_id=42, record_enriched_payload=payload)

    sqls_executed = [str(c.args[0]) for c in session.execute.call_args_list]
    assert str(expected_sql) in sqls_executed, f"Expected {tier} tier to execute its dedicated UPDATE constant"


def test_uncertain_path_binds_spam_score_none() -> None:
    """JIE #308 Fix B-2: uncertain UPDATE binds spam_score=None when classifier produced none."""
    session = _build_session()
    payload = _payload(tier="uncertain", score=None)

    apply_enrichment_to_job_postings(session, normalized_job_id=42, record_enriched_payload=payload)

    params = _params_for_sql(session, _UPDATE_UNCERTAIN_SQL)
    assert "spam_score" in params, "uncertain UPDATE missing spam_score bind"
    assert params["spam_score"] is None


def test_unhandled_tier_logs_error_not_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """JIE #308 Fix A: an unrecognized tier value must produce an ERROR log, not WARNING.

    Before Fix A this was a warning that buried in routine dashboards. Now it's
    a loud ERROR so a future regression introducing a new tier value (typo,
    case mismatch, classifier output drift) shows up in PR-CI noise immediately.

    The structurally-reachable way to hit this fall-through is for
    ``apply_spam_tiers`` to return a tier outside the four-state model
    (clean/flagged/rejected/uncertain). We simulate that via monkeypatch.
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
    payload = _payload(tier="bogus_tier", score=0.5)

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
    assert kwargs.get("tier_raw") == "bogus_tier"
    assert kwargs.get("spam_score_payload") == 0.5
