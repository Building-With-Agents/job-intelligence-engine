"""Configuration for fuzzy dedup (env + defaults). Documented in CONTEXT.md."""

from __future__ import annotations

import os

# Cosine similarity threshold (Decision #39 — EBS). Calibrate in Sprint 4 using FP/FN rates.
ENV_DEDUP_COSINE_THRESHOLD = "DEDUP_COSINE_THRESHOLD"
DEFAULT_DEDUP_COSINE_THRESHOLD = 0.92

# Rolling comparison window (days before anchor date).
DEDUP_ROLLING_WINDOW_DAYS = 30

# Anchor date expression on dbo.job_postings for the window.
# Uses COALESCE to prefer publish_date when present, falling back to the
# promoted date_posted column (Issue #172) so rows with NULL publish_date
# are still included in the dedup window.
JOB_POSTING_DATE_COLUMN = "COALESCE(publish_date, date_posted)"


def dedup_cosine_threshold() -> float:
    """Read threshold from env; default 0.92."""
    raw = os.getenv(ENV_DEDUP_COSINE_THRESHOLD)
    if raw is None or raw.strip() == "":
        return DEFAULT_DEDUP_COSINE_THRESHOLD
    return float(raw)
