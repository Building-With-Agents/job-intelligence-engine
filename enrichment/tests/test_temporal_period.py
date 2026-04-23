"""Unit tests for deterministic temporal period classification (UTC calendar date)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from enrichment.classifiers.temporal_period import classify_temporal_period

_LA = ZoneInfo("America/Los_Angeles")
_TOKYO = ZoneInfo("Asia/Tokyo")


@pytest.mark.parametrize(
    ("posted_date", "expected"),
    [
        (datetime(2022, 11, 30, tzinfo=timezone.utc), "pre_chatgpt"),
        (datetime(2022, 12, 1, tzinfo=timezone.utc), "early_genai"),
        (datetime(2023, 3, 31, tzinfo=timezone.utc), "early_genai"),
        (datetime(2023, 4, 1, tzinfo=timezone.utc), "post_gpt4"),
        (datetime(2024, 5, 31, tzinfo=timezone.utc), "post_gpt4"),
        (datetime(2024, 6, 1, tzinfo=timezone.utc), "agentic_era"),
    ],
)
def test_classify_temporal_period_boundaries(posted_date: datetime, expected: str) -> None:
    assert classify_temporal_period(posted_date) == expected


def test_classify_temporal_period_non_boundary_mid_bucket() -> None:
    assert classify_temporal_period(datetime(2023, 6, 15, 12, 0, tzinfo=timezone.utc)) == "post_gpt4"
    assert classify_temporal_period(datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)) == "agentic_era"


def test_classify_temporal_period_none_returns_none() -> None:
    assert classify_temporal_period(None) is None


def test_classify_temporal_period_naive_datetime_is_utc_wall_clock() -> None:
    """Naive datetimes use UTC wall clock (same components as UTC-aware → same bucket)."""
    assert (
        classify_temporal_period(datetime(2023, 6, 15, 12, 0, 0))
        == classify_temporal_period(datetime(2023, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
        == "post_gpt4"
    )


@pytest.mark.parametrize(
    ("posted_date", "expected"),
    [
        # Nov 30 / Dec 1 (UTC): America/Los_Angeles — same local calendar day, UTC flips.
        pytest.param(
            datetime(2022, 11, 30, 12, 0, tzinfo=_LA),
            "pre_chatgpt",
            id="la_nov30_noon_pst_utc_nov30",
        ),
        pytest.param(
            datetime(2022, 11, 30, 20, 0, tzinfo=_LA),
            "early_genai",
            id="la_nov30_evening_pst_utc_dec1",
        ),
        # Nov 30 / Dec 1 (UTC): Asia/Tokyo — local Dec 1 while UTC still Nov 30 (or vice versa).
        pytest.param(
            datetime(2022, 12, 1, 8, 59, tzinfo=_TOKYO),
            "pre_chatgpt",
            id="tokyo_dec1_morning_utc_nov30",
        ),
        pytest.param(
            datetime(2022, 12, 1, 9, 0, tzinfo=_TOKYO),
            "early_genai",
            id="tokyo_dec1_morning_utc_dec1",
        ),
        # Mar 31 / Apr 1 (UTC): Los Angeles — local Mar 31, UTC crosses at 17:00 PDT.
        pytest.param(
            datetime(2023, 3, 31, 16, 0, tzinfo=_LA),
            "early_genai",
            id="la_mar31_evening_utc_mar31",
        ),
        pytest.param(
            datetime(2023, 3, 31, 17, 0, tzinfo=_LA),
            "post_gpt4",
            id="la_mar31_evening_utc_apr1",
        ),
        # Mar 31 / Apr 1 (UTC): Tokyo — local Apr 1, UTC still Mar 31 until 09:00 JST.
        pytest.param(
            datetime(2023, 4, 1, 8, 59, tzinfo=_TOKYO),
            "early_genai",
            id="tokyo_apr1_morning_utc_mar31",
        ),
        pytest.param(
            datetime(2023, 4, 1, 9, 0, tzinfo=_TOKYO),
            "post_gpt4",
            id="tokyo_apr1_morning_utc_apr1",
        ),
        # May 31 / Jun 1 (UTC): Los Angeles — local May 31, UTC crosses at 17:00 PDT.
        pytest.param(
            datetime(2024, 5, 31, 16, 0, tzinfo=_LA),
            "post_gpt4",
            id="la_may31_evening_utc_may31",
        ),
        pytest.param(
            datetime(2024, 5, 31, 17, 0, tzinfo=_LA),
            "agentic_era",
            id="la_may31_evening_utc_jun1",
        ),
        # May 31 / Jun 1 (UTC): Tokyo — local Jun 1, UTC still May 31 until 09:00 JST.
        pytest.param(
            datetime(2024, 6, 1, 8, 59, tzinfo=_TOKYO),
            "post_gpt4",
            id="tokyo_jun1_morning_utc_may31",
        ),
        pytest.param(
            datetime(2024, 6, 1, 9, 0, tzinfo=_TOKYO),
            "agentic_era",
            id="tokyo_jun1_morning_utc_jun1",
        ),
    ],
)
def test_classify_temporal_period_transition_boundaries_use_utc_calendar_date(
    posted_date: datetime,
    expected: str,
) -> None:
    """Aware datetimes are converted to UTC; bucket follows UTC calendar date, not local date."""
    assert classify_temporal_period(posted_date) == expected
