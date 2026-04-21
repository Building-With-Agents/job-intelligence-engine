"""Field completeness counting for fuzzy dedup survivor arbitration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

_COMPLETENESS_FIELDS = (
    "salary_range",
    "location",
    "zip",
    "county",
    "job_description",
)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def completeness_score(row: dict[str, Any]) -> int:
    """
    Return a simple populated-field count for survivor selection.

    Week 6 issue semantics call for counting completeness, not weighting fields.
    Recency remains the tie-breaker in ``publish_date_for_tiebreak``.
    """
    return sum(1 for field in _COMPLETENESS_FIELDS if _has_value(row.get(field)))


def publish_date_for_tiebreak(row: dict[str, Any]) -> datetime | None:
    """Newer wins when completeness ties.

    Reads the ``publish_date`` row key. Note: the dedup SQL queries select
    ``jp.date_posted AS publish_date``, so this key carries the canonical
    ``date_posted`` value at runtime. The function name is preserved for
    callsite stability — rename in a follow-up when ``publish_date`` is
    fully dropped from ``job_postings``.
    """
    pd = row.get("publish_date")
    if pd is None:
        return None
    if isinstance(pd, datetime):
        return pd
    return None
