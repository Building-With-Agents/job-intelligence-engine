"""Tests for scripts/backfill_zip_codes_normalized.py — JIE #244."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.backfill_zip_codes_normalized as backfill_module  # noqa: E402


def test_module_importable() -> None:
    """Smoke: the script imports without error and exposes the public surface."""
    assert callable(backfill_module.backfill)
    assert callable(backfill_module.main)


def _engine_returning_rows(rows: list[tuple[int, str | None, str | None]]) -> MagicMock:
    """Build a mock engine whose .connect() context returns rows for the candidate query."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.execute.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine


def test_dry_run_counts_resolved_without_writes() -> None:
    """Dry-run mode counts resolutions and makes no session_scope writes."""
    fake_engine = _engine_returning_rows(
        [
            (1, "El Paso", "Texas"),
            (2, "Sterling", "Virginia"),
            (3, "Atlantis", "Wonderland"),  # unresolvable state
        ]
    )

    def fake_resolve(city: str | None, state: str | None, raw_zip: str | None) -> str | None:
        if state == "Texas":
            return "79901"
        if state == "Virginia":
            return "20164"
        return None

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "_resolve_zip_code", side_effect=fake_resolve),
        patch.object(backfill_module, "session_scope") as mock_scope,
    ):
        counts = backfill_module.backfill(limit=5000, apply=False)

    assert counts == {"candidates": 3, "resolved": 2, "unresolved": 1, "failed": 0}
    mock_scope.assert_not_called()


def test_no_candidates_returns_zero_counts() -> None:
    """When no candidate rows exist, all counts stay at 0."""
    fake_engine = _engine_returning_rows([])

    with patch.object(backfill_module, "get_engine", return_value=fake_engine):
        counts = backfill_module.backfill(limit=5000, apply=False)

    assert counts == {"candidates": 0, "resolved": 0, "unresolved": 0, "failed": 0}


def test_apply_writes_resolved_rows_and_skips_unresolved() -> None:
    """Apply mode writes UPDATEs for resolvable rows; unresolved rows are skipped without writes."""
    fake_engine = _engine_returning_rows(
        [
            (1, "El Paso", "Texas"),
            (2, "Atlantis", "Wonderland"),  # unresolvable
        ]
    )

    def fake_resolve(city: str | None, state: str | None, raw_zip: str | None) -> str | None:
        return "79901" if state == "Texas" else None

    session = MagicMock()
    scope_cm = MagicMock()
    scope_cm.__enter__.return_value = session
    scope_cm.__exit__.return_value = False

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "_resolve_zip_code", side_effect=fake_resolve),
        patch.object(backfill_module, "session_scope", return_value=scope_cm),
    ):
        counts = backfill_module.backfill(limit=5000, apply=True)

    assert counts == {"candidates": 2, "resolved": 1, "unresolved": 1, "failed": 0}
    # One resolved row -> two UPDATEs (normalized_jobs + job_postings)
    assert session.execute.call_count == 2


def test_apply_failure_in_session_scope_increments_failed() -> None:
    """Exceptions during apply are caught per-row and tallied as failures."""
    fake_engine = _engine_returning_rows([(1, "El Paso", "Texas")])

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "_resolve_zip_code", return_value="79901"),
        patch.object(backfill_module, "session_scope", side_effect=RuntimeError("DB exploded")),
    ):
        counts = backfill_module.backfill(limit=5000, apply=True)

    assert counts["failed"] == 1
    assert counts["resolved"] == 0


def test_candidate_query_uses_limit_param() -> None:
    """The fetch query receives the provided LIMIT bind parameter."""
    fake_engine = _engine_returning_rows([])

    with patch.object(backfill_module, "get_engine", return_value=fake_engine):
        backfill_module.backfill(limit=42, apply=False)

    conn = fake_engine.connect.return_value
    assert conn.execute.called
    _stmt, params = conn.execute.call_args[0]
    assert params == {"lim": 42}
