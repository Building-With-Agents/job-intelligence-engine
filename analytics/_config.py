"""Analytics agent config accessors backed by ``config/analytics.yaml``.

YAML is the source of truth — there are no Python-side defaults here.
"""

from __future__ import annotations

from common.config_loader import (
    cached_accessor,
    get_bool,
    get_int,
    get_optional_int,
)


@cached_accessor
def staleness_threshold_minutes() -> int:
    return get_int(
        file="analytics",
        key="analytics.staleness_threshold_minutes",
        env="STALENESS_THRESHOLD_MINUTES",
        minimum=1,
    )


@cached_accessor
def cardinality_cap() -> int:
    return get_int(
        file="analytics",
        key="analytics.cardinality_cap",
        env="CARDINALITY_CAP",
        minimum=1,
    )


@cached_accessor
def fresh_threshold_days() -> int:
    return get_int(
        file="analytics",
        key="analytics.fresh_threshold_days",
        env="FRESH_THRESHOLD_DAYS",
        minimum=1,
    )


@cached_accessor
def stale_threshold_days() -> int:
    return get_int(
        file="analytics",
        key="analytics.stale_threshold_days",
        env="STALE_THRESHOLD_DAYS",
        minimum=1,
    )


@cached_accessor
def disable_minimum_data_guard() -> bool:
    return get_bool(
        file="analytics",
        key="analytics.disable_minimum_data_guard",
        env="ANALYTICS_DISABLE_MINIMUM_DATA_GUARD",
    )


@cached_accessor
def clustering_load_limit() -> int | None:
    """Optional cap on records loaded for clustering. ``None`` = unlimited."""
    return get_optional_int(
        file="analytics",
        key="analytics.clustering_load_limit",
        env="ANALYTICS_CLUSTERING_LOAD_LIMIT",
        minimum=1,
    )


@cached_accessor
def query_row_limit() -> int:
    return get_int(
        file="analytics",
        key="analytics.query.row_limit",
        env="ANALYTICS_QUERY_LIMIT",
        minimum=1,
    )


@cached_accessor
def query_timeout_seconds() -> int:
    return get_int(
        file="analytics",
        key="analytics.query.timeout_seconds",
        env="ANALYTICS_QUERY_TIMEOUT_SECONDS",
        minimum=1,
    )


@cached_accessor
def qna_live() -> bool:
    return get_bool(
        file="analytics",
        key="analytics.qna_live",
        env="ANALYTICS_QNA_LIVE",
    )


@cached_accessor
def api_reload() -> bool:
    return get_bool(
        file="analytics",
        key="analytics.api.reload",
        env="ANALYTICS_API_RELOAD",
    )


__all__ = [
    "api_reload",
    "cardinality_cap",
    "clustering_load_limit",
    "disable_minimum_data_guard",
    "fresh_threshold_days",
    "qna_live",
    "query_row_limit",
    "query_timeout_seconds",
    "stale_threshold_days",
    "staleness_threshold_minutes",
]
