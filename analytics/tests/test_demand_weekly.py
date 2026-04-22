"""Regression tests for :mod:`analytics.aggregators.demand_weekly` SQL shape.

Guards the week-bucketing date source. publish_date is deprecated (99.26%
NULL, see ``docs/planning/QA_DATA_CONTRACT.md``) and must not appear in
generated SQL. date_posted is the canonical date column on job_postings
(promoted from normalized_jobs in Issue #172, fully backfilled).

Broader integration coverage lives in ``scripts/verify_aggregates.py``.
"""

from __future__ import annotations

from analytics.aggregators.demand_weekly import _SKILLS_EXPANDED, _TOOLS_EXPANDED


def _sql_text(stmt) -> str:
    return str(stmt).upper()


def test_skills_expanded_uses_jp_date_posted() -> None:
    """Skills SQL must use jp.date_posted and not reference deprecated publish_date."""
    sql = _sql_text(_SKILLS_EXPANDED)
    assert "JP.DATE_POSTED" in sql, "skills SQL must bucket by jp.date_posted"
    assert "PUBLISH_DATE" not in sql, "skills SQL must not reference deprecated publish_date"
    assert "NJ.DATE_POSTED" not in sql, (
        "skills SQL must use jp.date_posted (post-#172 promoted column), not nj.date_posted"
    )


def test_tools_expanded_uses_jp_date_posted() -> None:
    """Tools SQL must use jp.date_posted and not reference deprecated publish_date."""
    sql = _sql_text(_TOOLS_EXPANDED)
    assert "JP.DATE_POSTED" in sql, "tools SQL must bucket by jp.date_posted"
    assert "PUBLISH_DATE" not in sql, "tools SQL must not reference deprecated publish_date"
    assert "NJ.DATE_POSTED" not in sql, (
        "tools SQL must use jp.date_posted (post-#172 promoted column), not nj.date_posted"
    )
