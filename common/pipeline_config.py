"""Pipeline-wide config accessors backed by ``config/pipeline.yaml``.

YAML is the source of truth — there are no Python-side defaults here.
"""

from __future__ import annotations

from common.config_loader import (
    cached_accessor,
    get_float,
    get_int,
    get_optional_str,
)


@cached_accessor
def batch_size() -> int:
    return get_int(
        file="pipeline",
        key="pipeline.batch_size",
        env="BATCH_SIZE",
        minimum=1,
    )


@cached_accessor
def spam_flag_threshold() -> float:
    return get_float(
        file="pipeline",
        key="pipeline.spam.flag_threshold",
        env="SPAM_FLAG_THRESHOLD",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def spam_reject_threshold() -> float:
    return get_float(
        file="pipeline",
        key="pipeline.spam.reject_threshold",
        env="SPAM_REJECT_THRESHOLD",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def skill_confidence_threshold() -> float:
    return get_float(
        file="pipeline",
        key="pipeline.skill_confidence_threshold",
        env="SKILL_CONFIDENCE_THRESHOLD",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def db_pool_size() -> int:
    return get_int(
        file="pipeline",
        key="pipeline.database.pool_size",
        env="DB_POOL_SIZE",
        minimum=1,
    )


@cached_accessor
def db_max_overflow() -> int:
    return get_int(
        file="pipeline",
        key="pipeline.database.max_overflow",
        env="DB_MAX_OVERFLOW",
        minimum=0,
    )


@cached_accessor
def db_sql_schema() -> str | None:
    """Explicit schema override; ``None`` means callers should fall through to
    SQLAlchemy ORM-driven schema detection (preserves pre-migration semantics)."""
    raw = get_optional_str(
        file="pipeline",
        key="pipeline.database.sql_schema",
        env="JIE_SQL_SCHEMA",
    )
    if raw is None:
        return None
    normalized = raw.strip()
    return normalized or None


@cached_accessor
def normalization_batch_size() -> int:
    return get_int(
        file="pipeline",
        key="pipeline.normalization.batch_size",
        env="NORM_BATCH_SIZE",
        minimum=0,
    )


__all__ = [
    "batch_size",
    "db_max_overflow",
    "db_pool_size",
    "db_sql_schema",
    "normalization_batch_size",
    "skill_confidence_threshold",
    "spam_flag_threshold",
    "spam_reject_threshold",
]
