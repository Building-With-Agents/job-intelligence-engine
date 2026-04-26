"""Skills extraction config accessors backed by ``config/skills_extraction.yaml``.

YAML is the source of truth — there are no Python-side defaults here.
"""

from __future__ import annotations

from common.config_loader import cached_accessor, get_bool, get_float, get_int


@cached_accessor
def parallel_enabled() -> bool:
    return get_bool(
        file="skills_extraction",
        key="skills_extraction.parallel",
        env="SKILLS_EXTRACTION_PARALLEL",
    )


@cached_accessor
def parallel_concurrency() -> int:
    return get_int(
        file="skills_extraction",
        key="skills_extraction.concurrency",
        env="SKILLS_EXTRACTION_CONCURRENCY",
        minimum=1,
    )


@cached_accessor
def llm_timeout_seconds() -> int:
    return get_int(
        file="skills_extraction",
        key="skills_extraction.llm_timeout_seconds",
        env="SKILLS_EXTRACTION_LLM_TIMEOUT",
        minimum=1,
    )


@cached_accessor
def max_jobs() -> int:
    return get_int(
        file="skills_extraction",
        key="skills_extraction.max_jobs",
        env="SKILLS_EXTRACTION_MAX_JOBS",
        minimum=0,
    )


@cached_accessor
def fifo_fetch_size() -> int:
    """Bounded fetch size for FIFO mode (legacy ``NORM_BATCH_SIZE`` env override)."""
    return get_int(
        file="skills_extraction",
        key="skills_extraction.fifo_fetch_size",
        env="NORM_BATCH_SIZE",
        minimum=1,
    )


@cached_accessor
def serial_chunk_size() -> int:
    return get_int(
        file="skills_extraction",
        key="skills_extraction.serial.chunk_size",
        env="SKILLS_EXTRACTION_CHUNK_SIZE",
        minimum=0,
    )


@cached_accessor
def serial_chunk_cooldown() -> float:
    return get_float(
        file="skills_extraction",
        key="skills_extraction.serial.chunk_cooldown_seconds",
        env="SKILLS_EXTRACTION_CHUNK_COOLDOWN",
        minimum=0.0,
    )


@cached_accessor
def serial_inter_job_delay() -> float:
    return get_float(
        file="skills_extraction",
        key="skills_extraction.serial.delay_seconds",
        env="SKILLS_EXTRACTION_DELAY",
        minimum=0.0,
    )


@cached_accessor
def taxonomy_similarity_threshold() -> float:
    return get_float(
        file="skills_extraction",
        key="skills_extraction.taxonomy.similarity_threshold",
        env="SKILL_TAXONOMY_SIMILARITY_THRESHOLD",
        minimum=0.0,
        maximum=1.0,
    )


@cached_accessor
def embedding_inter_request_delay() -> float:
    return get_float(
        file="skills_extraction",
        key="skills_extraction.embedding.inter_request_delay_seconds",
        env="EMBEDDING_REQUEST_DELAY",
        minimum=0.0,
    )


@cached_accessor
def embedding_input_usd_per_1k_tokens() -> float:
    return get_float(
        file="skills_extraction",
        key="skills_extraction.embedding.input_usd_per_1k_tokens",
        env="EMBEDDING_INPUT_USD_PER_1K_TOKENS",
        minimum=0.0,
    )


__all__ = [
    "embedding_input_usd_per_1k_tokens",
    "embedding_inter_request_delay",
    "fifo_fetch_size",
    "llm_timeout_seconds",
    "max_jobs",
    "parallel_concurrency",
    "parallel_enabled",
    "serial_chunk_cooldown",
    "serial_chunk_size",
    "serial_inter_job_delay",
    "taxonomy_similarity_threshold",
]
