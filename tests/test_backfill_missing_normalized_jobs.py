"""Tests for scripts/backfill_missing_normalized_jobs.py — JIE #399."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.backfill_missing_normalized_jobs as backfill_module  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RECOVERABLE_ROW = {
    "posting_id": "aaaaaaaa-0000-0000-0000-000000000001",
    "source": "jsearch",
    "external_id": "ext-001",
    "ingestion_run_id": "run-abc",
    "raw_id": 101,
    "raw_status": "normalized",
}

_IRRECOVERABLE_ROW = {
    "posting_id": "bbbbbbbb-0000-0000-0000-000000000002",
    "source": "jsearch",
    "external_id": "ext-002",
    "ingestion_run_id": "run-abc",
    "raw_id": None,
    "raw_status": None,
}

_ALREADY_PENDING_ROW = {
    "posting_id": "cccccccc-0000-0000-0000-000000000003",
    "source": "jsearch",
    "external_id": "ext-003",
    "ingestion_run_id": "run-abc",
    "raw_id": 102,
    "raw_status": "pending",
}


def _engine_returning(rows: list[dict]) -> MagicMock:
    """Build a mock engine whose .connect() context manager returns the given rows."""
    cursor = MagicMock()
    cursor.mappings.return_value.all.return_value = [MagicMock(**r) for r in rows]
    # Make each returned MagicMock behave like a dict via __getitem__
    for mock_row, row_dict in zip(cursor.mappings.return_value.all.return_value, rows, strict=True):
        mock_row.__getitem__ = lambda self, k, _d=row_dict: _d[k]
        mock_row.keys.return_value = row_dict.keys()
    conn = MagicMock()
    conn.execute.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine


def _noop_session_scope():
    """Return a context manager factory that yields a MagicMock session."""
    @contextmanager
    def _cm():
        yield MagicMock()
    return _cm


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_module_importable() -> None:
    """The script imports cleanly and exposes the public surface."""
    assert callable(backfill_module.backfill)
    assert callable(backfill_module.main)


# ---------------------------------------------------------------------------
# Dry-run: no writes ever
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_writes() -> None:
    """Dry-run default must not call session_scope at all."""
    fake_engine = _engine_returning([_RECOVERABLE_ROW, _IRRECOVERABLE_ROW])

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "session_scope") as mock_scope,
    ):
        counts = backfill_module.backfill(limit=1000, apply=False)

    mock_scope.assert_not_called()
    assert counts["reset"] == 0
    assert counts["stamped"] == 0
    assert counts["failed"] == 0


# ---------------------------------------------------------------------------
# Early-return when there are no gap rows
# ---------------------------------------------------------------------------


def test_no_gap_rows_returns_zero_counts() -> None:
    """When the gap query returns nothing, all counts are zero and no writes happen."""
    fake_engine = _engine_returning([])

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "session_scope") as mock_scope,
    ):
        counts = backfill_module.backfill(limit=1000, apply=True)

    mock_scope.assert_not_called()
    assert counts == {
        "total_gap": 0,
        "recoverable": 0,
        "irrecoverable": 0,
        "reset": 0,
        "already_pending": 0,
        "stamped": 0,
        "failed": 0,
    }


# ---------------------------------------------------------------------------
# Apply: recoverable rows get reset
# ---------------------------------------------------------------------------


def test_apply_resets_recoverable_rows() -> None:
    """Rows with a raw_id and non-pending status are reset via session_scope."""
    fake_engine = _engine_returning([_RECOVERABLE_ROW])

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "session_scope", _noop_session_scope()),
    ):
        counts = backfill_module.backfill(limit=1000, apply=True)

    assert counts["reset"] == 1
    assert counts["already_pending"] == 0
    assert counts["failed"] == 0


# ---------------------------------------------------------------------------
# Apply: idempotency — already-pending rows are skipped
# ---------------------------------------------------------------------------


def test_apply_skips_already_pending() -> None:
    """A recoverable row that is already pending must not be double-reset."""
    fake_engine = _engine_returning([_ALREADY_PENDING_ROW])

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "session_scope") as mock_scope,
    ):
        counts = backfill_module.backfill(limit=1000, apply=True)

    # session_scope should never be called — the row is skipped before the try block
    mock_scope.assert_not_called()
    assert counts["already_pending"] == 1
    assert counts["reset"] == 0
    assert counts["failed"] == 0


# ---------------------------------------------------------------------------
# Apply: irrecoverable rows are stamped in job_postings
# ---------------------------------------------------------------------------


def test_apply_stamps_irrecoverable_rows() -> None:
    """Irrecoverable rows (no raw_id) are stamped via session_scope when --apply."""
    fake_engine = _engine_returning([_IRRECOVERABLE_ROW])

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "session_scope", _noop_session_scope()),
    ):
        counts = backfill_module.backfill(limit=1000, apply=True)

    assert counts["stamped"] == 1
    assert counts["failed"] == 0


# ---------------------------------------------------------------------------
# Apply: reset failure increments failed counter (not a silent swallow)
# ---------------------------------------------------------------------------


def test_apply_reset_failure_increments_failed_counter() -> None:
    """A DB exception during reset must increment counts['failed'], not swallow silently."""
    fake_engine = _engine_returning([_RECOVERABLE_ROW])

    @contextmanager
    def _failing_scope():
        raise RuntimeError("DB exploded")
        yield  # noqa: B901

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "session_scope", _failing_scope),
    ):
        counts = backfill_module.backfill(limit=1000, apply=True)

    assert counts["failed"] == 1
    assert counts["reset"] == 0


# ---------------------------------------------------------------------------
# Apply: stamp failure increments failed counter
# ---------------------------------------------------------------------------


def test_apply_stamp_failure_increments_failed_counter() -> None:
    """A DB exception during stamp must increment counts['failed']."""
    fake_engine = _engine_returning([_IRRECOVERABLE_ROW])

    @contextmanager
    def _failing_scope():
        raise RuntimeError("DB exploded")
        yield  # noqa: B901

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "session_scope", _failing_scope),
    ):
        counts = backfill_module.backfill(limit=1000, apply=True)

    assert counts["failed"] == 1
    assert counts["stamped"] == 0


# ---------------------------------------------------------------------------
# Gap query uses the limit bind parameter
# ---------------------------------------------------------------------------


def test_gap_query_uses_limit_param() -> None:
    """The :lim bind parameter is forwarded to the gap query."""
    fake_engine = _engine_returning([])

    with patch.object(backfill_module, "get_engine", return_value=fake_engine):
        backfill_module.backfill(limit=42, apply=False)

    conn = fake_engine.connect.return_value
    assert conn.execute.called
    _stmt, params = conn.execute.call_args[0]
    assert params == {"lim": 42}


# ---------------------------------------------------------------------------
# main() exit codes
# ---------------------------------------------------------------------------


def test_main_returns_0_when_no_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 0 when --apply succeeds with zero failures."""
    monkeypatch.setattr(
        backfill_module,
        "backfill",
        lambda **_: {
            "total_gap": 1,
            "recoverable": 1,
            "irrecoverable": 0,
            "reset": 1,
            "already_pending": 0,
            "stamped": 0,
            "failed": 0,
        },
    )
    monkeypatch.setattr(sys, "argv", ["backfill_missing_normalized_jobs.py", "--apply"])
    with patch.object(backfill_module, "load_repo_root_dotenv"):
        assert backfill_module.main() == 0


def test_main_returns_1_when_failures_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 1 when --apply produces at least one failure."""
    monkeypatch.setattr(
        backfill_module,
        "backfill",
        lambda **_: {
            "total_gap": 3,
            "recoverable": 3,
            "irrecoverable": 0,
            "reset": 2,
            "already_pending": 0,
            "stamped": 0,
            "failed": 1,
        },
    )
    monkeypatch.setattr(sys, "argv", ["backfill_missing_normalized_jobs.py", "--apply"])
    with patch.object(backfill_module, "load_repo_root_dotenv"):
        assert backfill_module.main() == 1


def test_main_returns_0_in_dry_run_even_if_failures_would_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run never returns 1 — failures only affect the exit code under --apply."""
    monkeypatch.setattr(
        backfill_module,
        "backfill",
        lambda **_: {
            "total_gap": 1,
            "recoverable": 0,
            "irrecoverable": 1,
            "reset": 0,
            "already_pending": 0,
            "stamped": 0,
            "failed": 0,
        },
    )
    monkeypatch.setattr(sys, "argv", ["backfill_missing_normalized_jobs.py"])
    with patch.object(backfill_module, "load_repo_root_dotenv"):
        assert backfill_module.main() == 0
