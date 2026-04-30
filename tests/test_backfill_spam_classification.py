"""Tests for scripts/backfill_spam_classification.py — JIE #308 PR-2."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.backfill_spam_classification as backfill_module  # noqa: E402


def test_module_importable() -> None:
    """Smoke: the script imports + exposes the public surface."""
    assert callable(backfill_module.backfill)
    assert callable(backfill_module.main)


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


def _spam_result(*, score: float | None) -> MagicMock:
    r = MagicMock()
    r.spam_score = score
    r.llm_cost_usd = 0.002
    r.cost_usd = 0.002
    return r


def test_dry_run_makes_no_classifier_or_writes() -> None:
    fake_engine = _engine_returning_rows([_candidate_row()])
    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "score_spam_preview") as mock_clf,
        patch.object(backfill_module, "session_scope") as mock_scope,
    ):
        counts = backfill_module.backfill(limit=5000, grace_minutes=0, apply=False)
    assert counts["candidates"] == 1
    mock_clf.assert_not_called()
    mock_scope.assert_not_called()


def test_apply_classifies_and_writes_via_spam_only_sql() -> None:
    fake_engine = _engine_returning_rows([_candidate_row()])
    session = MagicMock()
    scope_cm = MagicMock()
    scope_cm.__enter__.return_value = session
    scope_cm.__exit__.return_value = False

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "score_spam_preview", return_value=_spam_result(score=0.2)),
        patch.object(backfill_module, "session_scope", return_value=scope_cm),
    ):
        counts = backfill_module.backfill(limit=5000, grace_minutes=0, apply=True, max_cost_usd=10)

    assert counts["classified_clean"] == 1
    assert counts["cost_usd"] > 0
    params = session.execute.call_args.args[1]
    assert params["spam_tier"] == "clean"
    assert params["is_spam"] is False


def test_max_cost_cap_stops_processing_partway() -> None:
    """When accumulated cost exceeds --max-cost-usd, the loop stops gracefully."""
    rows = [_candidate_row(job_posting_id=f"00000000-0000-0000-0000-{i:012d}") for i in range(10)]
    fake_engine = _engine_returning_rows(rows)
    session = MagicMock()
    scope_cm = MagicMock()
    scope_cm.__enter__.return_value = session
    scope_cm.__exit__.return_value = False

    # Each row "costs" $0.50 — cap of $1.00 should stop after 2 rows
    expensive = MagicMock()
    expensive.spam_score = 0.2
    expensive.llm_cost_usd = 0.5
    expensive.cost_usd = 0.5

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "score_spam_preview", return_value=expensive),
        patch.object(backfill_module, "session_scope", return_value=scope_cm),
    ):
        counts = backfill_module.backfill(limit=5000, grace_minutes=0, apply=True, max_cost_usd=1.0)

    assert counts["candidates"] == 10
    # 2 classified before the cap kicks in (cost goes 0.5 → 1.0; on the 3rd
    # iteration accumulated_cost >= max_cost_usd so the loop breaks).
    assert counts["classified_clean"] == 2
    assert counts["stopped_on_cost_cap"] is True


def test_no_candidates_returns_zero_counts() -> None:
    fake_engine = _engine_returning_rows([])
    with patch.object(backfill_module, "get_engine", return_value=fake_engine):
        counts = backfill_module.backfill(limit=5000, grace_minutes=0, apply=False)
    assert counts["candidates"] == 0


def test_uncertain_path_when_classifier_returns_no_score() -> None:
    fake_engine = _engine_returning_rows([_candidate_row(extraction_failed=True)])
    session = MagicMock()
    scope_cm = MagicMock()
    scope_cm.__enter__.return_value = session
    scope_cm.__exit__.return_value = False

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "score_spam_preview", return_value=_spam_result(score=None)),
        patch.object(backfill_module, "session_scope", return_value=scope_cm),
    ):
        counts = backfill_module.backfill(limit=5000, grace_minutes=0, apply=True)

    assert counts["classified_uncertain"] == 1
    params = session.execute.call_args.args[1]
    assert params["is_spam"] is None
    assert params["spam_score"] is None
    assert params["spam_tier"] == "uncertain"


def test_failure_increments_failed_counter() -> None:
    fake_engine = _engine_returning_rows([_candidate_row()])
    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "score_spam_preview", side_effect=RuntimeError("LLM down")),
        patch.object(backfill_module, "session_scope") as mock_scope,
    ):
        counts = backfill_module.backfill(limit=5000, grace_minutes=0, apply=True)
    assert counts["failed"] == 1
    assert counts["classified_clean"] == 0
    mock_scope.assert_not_called()


def test_row_cost_falls_back_when_attrs_missing() -> None:
    """When a SpamPreviewResult lacks llm_cost_usd / cost_usd, use the fallback estimate."""

    class BareResult:
        spam_score = 0.2

    cost = backfill_module._row_cost(BareResult())
    assert cost == backfill_module._FALLBACK_ROW_COST_USD
