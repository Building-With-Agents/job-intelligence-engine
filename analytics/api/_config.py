"""LaborPulse API config accessors backed by ``config/laborpulse.yaml``.

YAML is the source of truth — there are no Python-side defaults here.
"""

from __future__ import annotations

from common.config_loader import cached_accessor, get_bool, get_float


@cached_accessor
def confidence_low_below() -> float:
    return get_float(
        file="laborpulse",
        key="laborpulse.confidence.low_below",
        env="LABORPULSE_CONF_LOW_BELOW",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def confidence_high_at_or_above() -> float:
    return get_float(
        file="laborpulse",
        key="laborpulse.confidence.high_at_or_above",
        env="LABORPULSE_CONF_HIGH_AT_OR_ABOVE",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def allow_no_api_keys() -> bool:
    return get_bool(
        file="laborpulse",
        key="laborpulse.allow_no_api_keys",
        env="LABORPULSE_ALLOW_NO_API_KEYS",
    )


@cached_accessor
def dashboard_query_mock() -> bool:
    return get_bool(
        file="laborpulse",
        key="laborpulse.dashboard_query_mock",
        env="DASHBOARD_ANALYTICS_QUERY_MOCK",
    )


__all__ = [
    "allow_no_api_keys",
    "confidence_high_at_or_above",
    "confidence_low_below",
    "dashboard_query_mock",
]
