"""Regression tests for :mod:`analytics.aggregators.demand_weekly` SQL shape.

Focused on the week-bucketing date source — after the #172 backfill, the
fallback date comes from ``jp.date_posted`` (the promoted column), not
``nj.date_posted``.  These tests guard against drift back to the pre-#172
pattern.  Broader integration coverage lives in ``scripts/verify_aggregates.py``.
"""

from __future__ import annotations

import re

from analytics.aggregators.demand_weekly import _SKILLS_EXPANDED, _TOOLS_EXPANDED


def _sql_text(stmt) -> str:
    return str(stmt).upper()


def test_skills_expanded_uses_jp_date_posted_fallback() -> None:
    """Skills SQL must fall back to ``jp.date_posted`` (promoted via #172)."""
    sql = _sql_text(_SKILLS_EXPANDED)
    assert re.search(r"COALESCE\(\s*JP\.PUBLISH_DATE\s*,\s*JP\.DATE_POSTED\s*\)", sql), (
        "skills SQL must use COALESCE(jp.publish_date, jp.date_posted)"
    )
    assert "NJ.DATE_POSTED" not in sql, (
        "skills SQL should no longer reference nj.date_posted — jp.date_posted "
        "is the backfilled source per #172 / PR #237 alignment"
    )


def test_tools_expanded_uses_jp_date_posted_fallback() -> None:
    """Tools SQL must fall back to ``jp.date_posted`` (promoted via #172)."""
    sql = _sql_text(_TOOLS_EXPANDED)
    assert re.search(r"COALESCE\(\s*JP\.PUBLISH_DATE\s*,\s*JP\.DATE_POSTED\s*\)", sql), (
        "tools SQL must use COALESCE(jp.publish_date, jp.date_posted)"
    )
    assert "NJ.DATE_POSTED" not in sql, (
        "tools SQL should no longer reference nj.date_posted — jp.date_posted "
        "is the backfilled source per #172 / PR #237 alignment"
    )
