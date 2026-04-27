"""Configuration accessors for fuzzy dedup.

Reads ``config/enrichment.yaml`` via ``common.config_loader``. Legacy env
vars (``DEDUP_COSINE_THRESHOLD``) still override per-accessor during the
deprecation window.
"""

from __future__ import annotations

# Cosine similarity threshold (Decision #39 — EBS). Calibrate in Sprint 4 using FP/FN rates.
ENV_DEDUP_COSINE_THRESHOLD = "DEDUP_COSINE_THRESHOLD"
DEFAULT_DEDUP_COSINE_THRESHOLD = 0.92

# Rolling comparison window (days before anchor date).
DEDUP_ROLLING_WINDOW_DAYS = 30

# Anchor date column on dbo.job_postings for the window.
# Uses date_posted (the canonical date column on job_postings, promoted from
# normalized_jobs in Issue #172 and fully backfilled). The legacy publish_date
# field is deprecated (see docs/planning/QA_DATA_CONTRACT.md) and must not
# appear in generated SQL.
JOB_POSTING_DATE_COLUMN = "date_posted"


def dedup_cosine_threshold() -> float:
    """Read threshold from config; default 0.92."""
    from enrichment._config import dedup_cosine_threshold as _accessor

    return _accessor()
