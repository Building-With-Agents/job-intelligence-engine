"""Ingestion-subsystem config accessors backed by ``config/ingestion.yaml``.

YAML is the source of truth — there are no Python-side defaults here.
"""

from __future__ import annotations

from common.config_loader import (
    cached_accessor,
    get_int,
    get_list,
    get_optional_str,
    get_str,
)

# ---------- JSearch search filters ----------


@cached_accessor
def jsearch_country() -> str:
    return (
        get_str(
            file="ingestion",
            key="ingestion.jsearch.country",
            env="JSEARCH_COUNTRY",
        )
        .strip()
        .lower()
    )


@cached_accessor
def jsearch_language() -> str:
    return (
        get_str(
            file="ingestion",
            key="ingestion.jsearch.language",
            env="JSEARCH_LANGUAGE",
        )
        .strip()
        .lower()
    )


@cached_accessor
def jsearch_date_posted() -> str:
    return (
        get_str(
            file="ingestion",
            key="ingestion.jsearch.date_posted",
            env="JSEARCH_DATE_POSTED",
        )
        .strip()
        .lower()
    )


@cached_accessor
def jsearch_start_key_index() -> int:
    return get_int(
        file="ingestion",
        key="ingestion.jsearch.start_key_index",
        env="JSEARCH_START_KEY_INDEX",
        minimum=1,
        maximum=4,
    )


@cached_accessor
def jsearch_max_pages() -> int:
    return get_int(
        file="ingestion",
        key="ingestion.jsearch.max_pages",
        env="JSEARCH_MAX_PAGES",
        minimum=1,
        maximum=50,
    )


# ---------- JSearch retry policy ----------


@cached_accessor
def jsearch_retry_max_retries() -> int:
    return get_int(
        file="ingestion",
        key="ingestion.jsearch.retry.max_retries",
        env="JSEARCH_MAX_RETRIES",
        minimum=0,
    )


@cached_accessor
def jsearch_retry_base_delay_seconds() -> int:
    return get_int(
        file="ingestion",
        key="ingestion.jsearch.retry.base_delay_seconds",
        env="JSEARCH_RETRY_BASE_DELAY_SECONDS",
        minimum=1,
    )


@cached_accessor
def jsearch_retry_max_delay_seconds() -> int:
    return get_int(
        file="ingestion",
        key="ingestion.jsearch.retry.max_delay_seconds",
        env="JSEARCH_RETRY_MAX_DELAY_SECONDS",
        minimum=1,
    )


# ---------- JSearch throttle ----------


@cached_accessor
def jsearch_rps() -> int:
    return get_int(
        file="ingestion",
        key="ingestion.jsearch.throttle.requests_per_second",
        env="JSEARCH_RPS",
        minimum=1,
    )


@cached_accessor
def jsearch_rpm() -> int:
    return get_int(
        file="ingestion",
        key="ingestion.jsearch.throttle.requests_per_minute",
        env="JSEARCH_RPM",
        minimum=1,
    )


# ---------- Scraping ----------


@cached_accessor
def scraping_targets() -> list[str]:
    return get_list(
        file="ingestion",
        key="ingestion.scraping_targets",
        env="SCRAPING_TARGETS",
    )


# ---------- Scheduler ----------


@cached_accessor
def scheduler_type() -> str:
    return get_str(
        file="ingestion",
        key="ingestion.scheduler.type",
        env="SCHEDULER_TYPE",
    ).strip()


@cached_accessor
def scheduler_interval_minutes() -> int:
    return get_int(
        file="ingestion",
        key="ingestion.scheduler.interval_minutes",
        env="INGESTION_INTERVAL_MINUTES",
        minimum=1,
    )


@cached_accessor
def scheduler_cron_expression() -> str | None:
    raw = get_optional_str(
        file="ingestion",
        key="ingestion.scheduler.cron_expression",
        env="INGESTION_CRON_EXPRESSION",
    )
    if raw is None:
        return None
    s = raw.strip()
    return s or None


@cached_accessor
def scheduler_state_path() -> str | None:
    raw = get_optional_str(
        file="ingestion",
        key="ingestion.scheduler.state_path",
        env="SCHEDULER_STATE_PATH",
    )
    if raw is None:
        return None
    s = raw.strip()
    return s or None


__all__ = [
    "jsearch_country",
    "jsearch_date_posted",
    "jsearch_language",
    "jsearch_max_pages",
    "jsearch_retry_base_delay_seconds",
    "jsearch_retry_max_delay_seconds",
    "jsearch_retry_max_retries",
    "jsearch_rpm",
    "jsearch_rps",
    "jsearch_start_key_index",
    "scheduler_cron_expression",
    "scheduler_interval_minutes",
    "scheduler_state_path",
    "scheduler_type",
    "scraping_targets",
]
