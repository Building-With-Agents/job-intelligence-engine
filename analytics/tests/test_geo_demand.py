"""Unit tests for :mod:`analytics.aggregators.geo_demand`."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from analytics.aggregators.geo_demand import compute_geo_demand_weekly


def _pg_session() -> MagicMock:
    session = MagicMock()
    bind = MagicMock()
    bind.dialect.name = "postgresql"
    session.get_bind.return_value = bind
    return session


def _executed_sql(session: MagicMock) -> str:
    """Return the SQL string passed to session.execute."""
    stmt = session.execute.call_args[0][0]
    return str(stmt)


def test_compute_geo_demand_weekly_non_postgresql_raises() -> None:
    session = MagicMock()
    bind = MagicMock()
    bind.dialect.name = "sqlite"
    session.get_bind.return_value = bind
    with pytest.raises(NotImplementedError, match="PostgreSQL"):
        compute_geo_demand_weekly(session, date(2026, 1, 5))


def test_compute_geo_demand_weekly_returns_orm_rows() -> None:
    session = _pg_session()
    ws = date(2026, 1, 5)
    mappings = MagicMock()
    mappings.all.return_value = [
        {"region": "el_paso", "cnt": 42},
        {"region": "las_cruces", "cnt": 7},
    ]
    session.execute.return_value.mappings.return_value = mappings

    rows = compute_geo_demand_weekly(session, ws)

    assert len(rows) == 2
    assert rows[0].week_start == ws
    assert rows[0].borderplex_subregion == "el_paso"
    assert rows[0].posting_count == 42
    assert rows[1].borderplex_subregion == "las_cruces"
    assert rows[1].posting_count == 7
    session.execute.assert_called_once()
    bound = session.execute.call_args[0][1]
    assert bound["week_start_ts"].date() == ws
    assert bound["week_end_ts"].date() == date(2026, 1, 12)


def test_compute_geo_demand_weekly_empty() -> None:
    session = _pg_session()
    mappings = MagicMock()
    mappings.all.return_value = []
    session.execute.return_value.mappings.return_value = mappings

    rows = compute_geo_demand_weekly(session, date(2025, 12, 1))
    assert rows == []


def test_compute_geo_demand_weekly_sql_uses_coalesce_fallback() -> None:
    """SQL must use COALESCE(publish_date, date_posted) — not publish_date alone.

    Postings promoted via Issue #172 carry date_posted but NULL publish_date.
    A filter on publish_date IS NOT NULL would silently drop the vast majority
    of the corpus.  This test guards against regression to the old behaviour.
    """
    session = _pg_session()
    mappings = MagicMock()
    mappings.all.return_value = []
    session.execute.return_value.mappings.return_value = mappings

    compute_geo_demand_weekly(session, date(2026, 4, 13))

    sql = _executed_sql(session).upper()
    assert "COALESCE" in sql, "SQL must use COALESCE to fall back to date_posted"
    assert "DATE_POSTED" in sql, "SQL must reference date_posted as the COALESCE fallback"


def test_compute_geo_demand_weekly_date_window_binds() -> None:
    """Week window parameters must span exactly 7 days and use UTC datetimes."""
    from datetime import timezone

    session = _pg_session()
    mappings = MagicMock()
    mappings.all.return_value = []
    session.execute.return_value.mappings.return_value = mappings

    ws = date(2026, 4, 13)
    compute_geo_demand_weekly(session, ws)

    bound = session.execute.call_args[0][1]
    ts_start = bound["week_start_ts"]
    ts_end = bound["week_end_ts"]

    assert ts_start.tzinfo == timezone.utc
    assert ts_end.tzinfo == timezone.utc
    assert (ts_end - ts_start).days == 7
