"""Tests for scripts/sweep_unclassified_spam.py — JIE #308 Fix C."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.sweep_unclassified_spam as sweep_module  # noqa: E402


def test_module_importable() -> None:
    """Smoke: the script imports + exposes the public surface."""
    assert callable(sweep_module.sweep)
    assert callable(sweep_module.main)


def _engine_returning_rows(rows: list[dict]) -> MagicMock:
    """Mock engine whose .connect() returns rows for the candidate query."""
    mappings = MagicMock()
    mappings.all.return_value = rows
    cursor = MagicMock()
    cursor.mappings.return_value = mappings
    conn = MagicMock()
    conn.execute.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine


def _candidate_row(**overrides) -> dict:
    base = {
        "job_posting_id": "00000000-0000-0000-0000-000000000001",
        "job_title": "Software Engineer",
        "job_description": "Build things.",
        "skills": [{"skill_label": "Python"}],
        "tools": None,
        "tasks": None,
        "responsibilities": None,
        "context": None,
        "extraction_failed": False,
    }
    base.update(overrides)
    return base


def _spam_result(*, score: float | None, tier: str = "clean") -> MagicMock:
    r = MagicMock()
    r.spam_score = score
    r.tier = tier
    r.is_spam = False if tier == "clean" else None if tier == "flagged" else True if tier == "rejected" else None
    r.degraded = score is None
    return r


def test_dry_run_counts_candidates_without_writes_or_llm() -> None:
    """Dry-run mode counts candidates only — no session_scope, no classifier call."""
    fake_engine = _engine_returning_rows(
        [_candidate_row(), _candidate_row(job_posting_id="00000000-0000-0000-0000-000000000002")]
    )

    with (
        patch.object(sweep_module, "get_engine", return_value=fake_engine),
        patch.object(sweep_module, "score_spam_preview") as mock_classifier,
        patch.object(sweep_module, "session_scope") as mock_scope,
    ):
        counts = sweep_module.sweep(limit=200, grace_minutes=60, apply=False)

    assert counts["candidates"] == 2
    assert counts["classified_clean"] == 0
    mock_classifier.assert_not_called()
    mock_scope.assert_not_called()


def test_no_candidates_returns_zero_counts() -> None:
    """When the candidate query returns zero rows, all counts stay at 0."""
    fake_engine = _engine_returning_rows([])

    with patch.object(sweep_module, "get_engine", return_value=fake_engine):
        counts = sweep_module.sweep(limit=200, grace_minutes=60, apply=False)

    assert counts["candidates"] == 0


def test_apply_classifies_clean_writes_via_spam_only_sql() -> None:
    """Apply: classifier returns score=0.2 → tier=clean → UPDATE writes is_spam=False."""
    fake_engine = _engine_returning_rows([_candidate_row()])

    session = MagicMock()
    scope_cm = MagicMock()
    scope_cm.__enter__.return_value = session
    scope_cm.__exit__.return_value = False

    with (
        patch.object(sweep_module, "get_engine", return_value=fake_engine),
        patch.object(
            sweep_module,
            "score_spam_preview",
            return_value=_spam_result(score=0.2, tier="clean"),
        ),
        patch.object(sweep_module, "session_scope", return_value=scope_cm),
    ):
        counts = sweep_module.sweep(limit=200, grace_minutes=60, apply=True)

    assert counts["candidates"] == 1
    assert counts["classified_clean"] == 1
    # The UPDATE bind params include is_spam=False, spam_tier='clean'.
    update_call = session.execute.call_args
    params = update_call.args[1]
    assert params["is_spam"] is False
    assert params["spam_tier"] == "clean"
    assert params["spam_score"] == 0.2


def test_apply_classifies_flagged() -> None:
    """Apply: score=0.8 → tier=flagged → is_spam=None."""
    fake_engine = _engine_returning_rows([_candidate_row()])
    session = MagicMock()
    scope_cm = MagicMock()
    scope_cm.__enter__.return_value = session
    scope_cm.__exit__.return_value = False

    with (
        patch.object(sweep_module, "get_engine", return_value=fake_engine),
        patch.object(sweep_module, "score_spam_preview", return_value=_spam_result(score=0.8, tier="flagged")),
        patch.object(sweep_module, "session_scope", return_value=scope_cm),
    ):
        counts = sweep_module.sweep(limit=200, grace_minutes=60, apply=True)

    assert counts["classified_flagged"] == 1
    params = session.execute.call_args.args[1]
    assert params["is_spam"] is None
    assert params["spam_tier"] == "flagged"


def test_apply_uncertain_when_classifier_returns_no_score() -> None:
    """Apply: score=None (degraded) → tier=uncertain → is_spam=NULL, spam_score=NULL."""
    fake_engine = _engine_returning_rows([_candidate_row(extraction_failed=True)])
    session = MagicMock()
    scope_cm = MagicMock()
    scope_cm.__enter__.return_value = session
    scope_cm.__exit__.return_value = False

    with (
        patch.object(sweep_module, "get_engine", return_value=fake_engine),
        patch.object(sweep_module, "score_spam_preview", return_value=_spam_result(score=None)),
        patch.object(sweep_module, "session_scope", return_value=scope_cm),
    ):
        counts = sweep_module.sweep(limit=200, grace_minutes=60, apply=True)

    assert counts["classified_uncertain"] == 1
    params = session.execute.call_args.args[1]
    assert params["is_spam"] is None
    assert params["spam_score"] is None
    assert params["spam_tier"] == "uncertain"


def test_apply_failure_increments_failed_counter() -> None:
    """An exception during a row's classification is caught and tallied as failure."""
    fake_engine = _engine_returning_rows([_candidate_row()])

    with (
        patch.object(sweep_module, "get_engine", return_value=fake_engine),
        patch.object(sweep_module, "score_spam_preview", side_effect=RuntimeError("LLM exploded")),
        patch.object(sweep_module, "session_scope") as mock_scope,
    ):
        counts = sweep_module.sweep(limit=200, grace_minutes=60, apply=True)

    assert counts["failed"] == 1
    assert counts["classified_clean"] == 0
    mock_scope.assert_not_called()


def test_grace_window_bind_param() -> None:
    """The candidate query receives the provided grace_minutes."""
    fake_engine = _engine_returning_rows([])

    with patch.object(sweep_module, "get_engine", return_value=fake_engine):
        sweep_module.sweep(limit=200, grace_minutes=42, apply=False)

    conn = fake_engine.connect.return_value
    _stmt, params = conn.execute.call_args.args
    assert params == {"lim": 200, "grace_minutes": 42}
