"""Enrichment-subsystem config accessors backed by ``config/enrichment.yaml``.

YAML is the source of truth — there are no Python-side defaults here.
"""

from __future__ import annotations

from common.config_loader import cached_accessor, get_bool, get_float, get_int

ENRICHMENT_CONCURRENCY_MAX = 5  # issue #149 — async DB pool exhaustion above 5


@cached_accessor
def enrichment_parallel() -> bool:
    return get_bool(
        file="enrichment",
        key="enrichment.parallel",
        env="ENRICHMENT_PARALLEL",
    )


@cached_accessor
def enrichment_concurrency() -> int:
    return get_int(
        file="enrichment",
        key="enrichment.concurrency",
        env="ENRICHMENT_CONCURRENCY",
        minimum=1,
        maximum=ENRICHMENT_CONCURRENCY_MAX,
    )


@cached_accessor
def enrichment_llm_timeout_seconds() -> int:
    return get_int(
        file="enrichment",
        key="enrichment.llm_timeout_seconds",
        env="ENRICHMENT_LLM_TIMEOUT",
        minimum=1,
    )


@cached_accessor
def enrichment_max_retries() -> int:
    """Additional attempts after the first timeout in ``enrich_record_async``.

    YAML key: ``enrichment.max_retries`` (default 1 → one retry → max 2 total).
    Env override: ``ENRICHMENT_MAX_RETRIES``. Set 0 to match pre-#150 behavior.
    """
    return get_int(
        file="enrichment",
        key="enrichment.max_retries",
        env="ENRICHMENT_MAX_RETRIES",
        minimum=0,
    )


@cached_accessor
def dedup_cosine_threshold() -> float:
    return get_float(
        file="enrichment",
        key="enrichment.dedup.cosine_threshold",
        env="DEDUP_COSINE_THRESHOLD",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def dedup_rolling_window_days() -> int:
    return get_int(
        file="enrichment",
        key="enrichment.dedup.rolling_window_days",
        env="DEDUP_ROLLING_WINDOW_DAYS",
        minimum=1,
    )


@cached_accessor
def spam_preview_allow_heuristic() -> bool:
    return get_bool(
        file="enrichment",
        key="enrichment.spam_preview.allow_heuristic",
        env="SPAM_PREVIEW_ALLOW_HEURISTIC",
    )


@cached_accessor
def soc_unclassified_rate_threshold() -> float:
    """Fraction of enriched records allowed to be SOC-unclassified before an alert fires.

    Reads ``enrichment.soc.unclassified_rate_threshold`` from
    ``config/enrichment.yaml`` (env override ``SOC_UNCLASSIFIED_RATE_THRESHOLD``).
    Returns a value in [0.0, 1.0]; the production default is 0.10 (10 %).
    """
    return get_float(
        file="enrichment",
        key="enrichment.soc.unclassified_rate_threshold",
        env="SOC_UNCLASSIFIED_RATE_THRESHOLD",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def esco_seed_apply_filter() -> bool:
    return get_bool(
        file="enrichment",
        key="enrichment.esco_seed.apply_filter",
        env="ESCO_SEED_APPLY_FILTER",
    )


__all__ = [
    "dedup_cosine_threshold",
    "dedup_rolling_window_days",
    "enrichment_concurrency",
    "enrichment_llm_timeout_seconds",
    "enrichment_max_retries",
    "enrichment_parallel",
    "esco_seed_apply_filter",
    "soc_unclassified_rate_threshold",
    "ENRICHMENT_CONCURRENCY_MAX",
    "spam_preview_allow_heuristic",
]
