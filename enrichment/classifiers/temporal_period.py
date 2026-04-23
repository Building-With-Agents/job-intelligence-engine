"""Deterministic job-posting temporal buckets from a single posted timestamp.

Classification compares the **UTC calendar date** of ``posted_date`` (the date
portion after normalizing to UTC). Timezone-aware values are converted with
``astimezone(timezone.utc)`` before ``.date()``; naive datetimes are treated as
UTC wall time (``replace(tzinfo=UTC)``) so the result does not depend on the
host local timezone.

Maps to string labels aligned with ``dbo.job_postings.temporal_period`` (TEXT).
No LLM; boundaries are fixed module-level dates.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

# Inclusive-range boundaries on the UTC calendar.
DATE_EARLY_GENAI_START = date(2022, 12, 1)
DATE_EARLY_GENAI_END = date(2023, 3, 31)
DATE_POST_GPT4_START = date(2023, 4, 1)
DATE_POST_GPT4_END = date(2024, 5, 31)
DATE_AGENTIC_ERA_START = date(2024, 6, 1)


def _utc_calendar_date(posted_date: datetime) -> date:
    """Calendar date in UTC: aware values are converted to UTC first; naive means UTC wall clock."""
    if posted_date.tzinfo is None:
        return posted_date.replace(tzinfo=timezone.utc).date()
    return posted_date.astimezone(timezone.utc).date()


def classify_temporal_period(posted_date: datetime | None) -> str | None:
    """Return temporal bucket for ``posted_date``, or ``None`` if input is ``None``.

    All boundary checks use ``utc_date``, the UTC calendar date of ``posted_date``.

    Buckets (``utc_date``):
    - ``pre_chatgpt``: utc_date < 2022-12-01
    - ``early_genai``: 2022-12-01 <= utc_date <= 2023-03-31
    - ``post_gpt4``: 2023-04-01 <= utc_date <= 2024-05-31
    - ``agentic_era``: utc_date >= 2024-06-01
    """
    if posted_date is None:
        return None

    utc_date = _utc_calendar_date(posted_date)

    if utc_date < DATE_EARLY_GENAI_START:
        return "pre_chatgpt"
    if utc_date <= DATE_EARLY_GENAI_END:
        return "early_genai"
    if DATE_POST_GPT4_START <= utc_date <= DATE_POST_GPT4_END:
        return "post_gpt4"
    # Remaining UTC dates: utc_date > DATE_POST_GPT4_END ⇒ utc_date >= DATE_AGENTIC_ERA_START.
    return "agentic_era"
