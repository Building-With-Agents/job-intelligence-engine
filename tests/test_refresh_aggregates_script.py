"""Unit tests for ``scripts.refresh_aggregates`` isolation contract.

Converts the Week 9 checklist bullet "if a computation fails, log the error
and continue to the next table — do not halt the entire refresh" from a
structural, code-reviewed claim into a runtime-enforced guarantee.

No database access: ``session_scope`` and ``_count_rows_for_week`` are
monkey-patched so the tests only exercise ``_run_refresh`` / ``_skipped``
control flow and ``StepResult`` book-keeping.
"""

from __future__ import annotations

import contextlib
from datetime import date

import pytest

from scripts import refresh_aggregates as ra


class _FakeModel:
    """Stand-in for a SQLAlchemy model. ``_run_refresh`` only reads attributes."""

    week_start = object()


@pytest.fixture
def patched_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch DB-touching helpers so each test stays pure unit."""

    @contextlib.contextmanager
    def _fake_session_scope():
        yield object()

    monkeypatch.setattr(ra, "_count_rows_for_week", lambda *_a, **_kw: 0)
    monkeypatch.setattr(ra, "session_scope", _fake_session_scope)


def _spec(name: str, callable_: ra.Callable[..., object]) -> ra._TableSpec:
    return ra._TableSpec(
        name=name,
        model=_FakeModel,
        week_column=_FakeModel.week_start,
        refresh_callable=callable_,
    )


def test_run_refresh_records_success(patched_io: None) -> None:
    """Happy path: callable returns cleanly → StepResult status=success."""

    def _ok(_session: object, _week: date) -> None:
        return None

    result = ra._run_refresh(_spec("fake_table", _ok), date(2026, 4, 13))

    assert result.status == ra.STATUS_SUCCESS
    assert result.error is None
    assert result.skip_reason is None
    assert result.rows_before == 0
    assert result.rows_after == 0
    assert result.rows_delta == 0
    assert result.duration_ms >= 0.0
    assert result.name == "fake_table"


def test_run_refresh_catches_exception_and_records_failure(patched_io: None) -> None:
    """Core contract: raising callable → failure recorded, no re-raise."""

    def _boom(_session: object, _week: date) -> None:
        raise RuntimeError("simulated aggregator crash")

    result = ra._run_refresh(_spec("broken_table", _boom), date(2026, 4, 13))

    assert result.status == ra.STATUS_FAILURE
    assert result.error is not None
    assert "simulated aggregator crash" in result.error
    assert result.duration_ms >= 0.0


def test_run_refresh_truncates_long_error(patched_io: None) -> None:
    """Long exception messages are truncated to ``_ERROR_TRUNCATE`` chars."""
    long_msg = "x" * (ra._ERROR_TRUNCATE + 500)

    def _boom(_session: object, _week: date) -> None:
        raise RuntimeError(long_msg)

    result = ra._run_refresh(_spec("verbose_failure", _boom), date(2026, 4, 13))

    assert result.status == ra.STATUS_FAILURE
    assert result.error is not None
    assert len(result.error) <= ra._ERROR_TRUNCATE


def test_skipped_helper_returns_skipped_status() -> None:
    """``_skipped`` captures the dependency short-circuit used when a parent fails."""
    result = ra._skipped(
        "skill_velocity",
        "dependency skill_demand_weekly not successful",
    )

    assert result.status == ra.STATUS_SKIPPED
    assert result.skip_reason == "dependency skill_demand_weekly not successful"
    assert result.error is None
    assert result.rows_before is None
    assert result.rows_after is None
    assert result.rows_delta is None
    assert result.duration_ms == 0.0
