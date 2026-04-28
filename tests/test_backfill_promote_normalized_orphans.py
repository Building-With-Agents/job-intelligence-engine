"""Tests for scripts/backfill_promote_normalized_orphans.py — JIE #289."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Importing the module exercises the script-level imports + sys.path manipulation.
import scripts.backfill_promote_normalized_orphans as backfill_module  # noqa: E402


def test_module_importable() -> None:
    """Smoke: the script imports without error and exposes the public surface."""
    assert callable(backfill_module.backfill)
    assert callable(backfill_module.main)


def _engine_returning_orphan_ids(ids: list[int]) -> MagicMock:
    """Build a mock engine whose .connect() context returns rows for the orphan query."""
    rows = [(i,) for i in ids]
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn = MagicMock()
    conn.execute.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    engine = MagicMock()
    engine.connect.return_value = conn
    return engine


def test_dry_run_counts_orphans_without_writes() -> None:
    """Dry-run mode reports orphan count and makes no session_scope writes."""
    fake_engine = _engine_returning_orphan_ids([10, 20, 30])

    with (
        patch.object(backfill_module, "get_engine", return_value=fake_engine),
        patch.object(backfill_module, "session_scope") as mock_scope,
    ):
        counts = backfill_module.backfill(limit=1000, apply=False)

    assert counts == {"orphans": 3, "promoted": 0, "already_present": 0, "failed": 0}
    # Dry-run must NOT open any write session
    mock_scope.assert_not_called()


def test_dry_run_with_no_orphans() -> None:
    """When the orphan query returns zero rows, all counts stay at 0."""
    fake_engine = _engine_returning_orphan_ids([])

    with patch.object(backfill_module, "get_engine", return_value=fake_engine):
        counts = backfill_module.backfill(limit=1000, apply=False)

    assert counts == {"orphans": 0, "promoted": 0, "already_present": 0, "failed": 0}


@pytest.mark.parametrize("orphan_count,limit", [(0, 1000), (5, 1000), (3, 2)])
def test_orphan_count_respects_limit(orphan_count: int, limit: int) -> None:
    """The fetch query uses the provided LIMIT — verify the bind parameter."""
    fake_engine = _engine_returning_orphan_ids(list(range(orphan_count)))

    with patch.object(backfill_module, "get_engine", return_value=fake_engine):
        backfill_module.backfill(limit=limit, apply=False)

    # The orphan-fetch execute call should have been made with our limit
    conn = fake_engine.connect.return_value
    assert conn.execute.called
    _stmt, params = conn.execute.call_args[0]
    assert params == {"lim": limit}
