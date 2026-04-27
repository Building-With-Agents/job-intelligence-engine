"""Migration parity test: every YAML-backed accessor returns the historical
pre-migration default when the legacy env var is unset.

Two checks per accessor:
  1. **Value parity:** YAML value matches the original Python-side default
     (no value drift introduced by the migration).
  2. **Loader wiring:** with the env var deleted, the accessor still returns
     the expected value — proving it's reading YAML, not falling through to
     a stale env entry.

If a value drifts or a YAML key goes missing, this test breaks loudly with
the dotted YAML path you need to fix.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

# Every (accessor, legacy_env, expected_historical_value) tuple. Sorted by
# subsystem to mirror the YAML files. Add new accessors here when migrating
# new variables.
#
# `accessor` is imported lazily inside the test; we reference it by string
# so this file can be imported even before the modules are wired up.
PARITY: list[tuple[str, str | None, Any]] = [
    # ---------- pipeline.yaml ----------
    ("common.pipeline_config:batch_size", "BATCH_SIZE", 100),
    ("common.pipeline_config:spam_flag_threshold", "SPAM_FLAG_THRESHOLD", 0.7),
    ("common.pipeline_config:spam_reject_threshold", "SPAM_REJECT_THRESHOLD", 0.9),
    ("common.pipeline_config:skill_confidence_threshold", "SKILL_CONFIDENCE_THRESHOLD", 0.75),
    ("common.pipeline_config:db_pool_size", "DB_POOL_SIZE", 30),
    ("common.pipeline_config:db_max_overflow", "DB_MAX_OVERFLOW", 20),
    # `pipeline.normalization.batch_size` was 0 (unlimited) in the original
    # normalization/agent.py read — preserved.
    ("common.pipeline_config:normalization_batch_size", "NORM_BATCH_SIZE", 0),
    # `db_sql_schema` is optional; YAML stores `~` (null) so the accessor
    # returns None and callers fall through to ORM-driven detection.
    ("common.pipeline_config:db_sql_schema", "JIE_SQL_SCHEMA", None),
    # ---------- clustering.yaml ----------
    ("analytics.clustering.config:cluster_embedding_batch_size", "CLUSTER_EMBEDDING_BATCH_SIZE", 50),
    (
        "analytics.clustering.config:cluster_embedding_audit_agent_name",
        "CLUSTER_EMBEDDING_AUDIT_AGENT_NAME",
        "analytics-clustering",
    ),
    ("analytics.clustering.config:cluster_min_total_postings", "CLUSTER_MIN_TOTAL_POSTINGS", 500),
    ("analytics.clustering.config:cluster_min_cluster_size", "CLUSTER_MIN_CLUSTER_SIZE", 10),
    ("analytics.clustering.config:cluster_min_samples", "CLUSTER_MIN_SAMPLES", 5),
    ("analytics.clustering.config:cluster_selection_epsilon", "CLUSTER_SELECTION_EPSILON", 0.0),
    ("analytics.clustering.config:cluster_distance_metric", "CLUSTER_DISTANCE_METRIC", "euclidean"),
    ("analytics.clustering.config:cluster_label_dominance_threshold", "CLUSTER_LABEL_DOMINANCE_THRESHOLD", 0.30),
    ("analytics.clustering.config:emergence_min_quality_score", "EMERGENCE_MIN_QUALITY_SCORE", 0.70),
    ("analytics.clustering.config:emergence_min_novel_skills", "EMERGENCE_MIN_NOVEL_SKILLS", 3),
    ("analytics.clustering.config:emergence_min_distinct_employers", "EMERGENCE_MIN_DISTINCT_EMPLOYERS", 2),
    # ---------- enrichment.yaml ----------
    # parallel is currently False — async path hangs; flip back to True
    # once the in-flight enrichment-async fix PR lands.
    ("enrichment._config:enrichment_parallel", "ENRICHMENT_PARALLEL", False),
    ("enrichment._config:enrichment_concurrency", "ENRICHMENT_CONCURRENCY", 5),
    ("enrichment._config:enrichment_llm_timeout_seconds", "ENRICHMENT_LLM_TIMEOUT", 120),
    ("enrichment._config:dedup_cosine_threshold", "DEDUP_COSINE_THRESHOLD", 0.92),
    ("enrichment._config:dedup_rolling_window_days", "DEDUP_ROLLING_WINDOW_DAYS", 30),
    ("enrichment._config:spam_preview_allow_heuristic", "SPAM_PREVIEW_ALLOW_HEURISTIC", False),
    ("enrichment._config:esco_seed_apply_filter", "ESCO_SEED_APPLY_FILTER", False),
    # ---------- skills_extraction.yaml ----------
    ("skills_extraction._config:parallel_enabled", "SKILLS_EXTRACTION_PARALLEL", True),
    ("skills_extraction._config:parallel_concurrency", "SKILLS_EXTRACTION_CONCURRENCY", 5),
    ("skills_extraction._config:llm_timeout_seconds", "SKILLS_EXTRACTION_LLM_TIMEOUT", 120),
    ("skills_extraction._config:max_jobs", "SKILLS_EXTRACTION_MAX_JOBS", 0),
    ("skills_extraction._config:serial_chunk_size", "SKILLS_EXTRACTION_CHUNK_SIZE", 5),
    ("skills_extraction._config:serial_chunk_cooldown", "SKILLS_EXTRACTION_CHUNK_COOLDOWN", 30.0),
    ("skills_extraction._config:serial_inter_job_delay", "SKILLS_EXTRACTION_DELAY", 1.0),
    ("skills_extraction._config:taxonomy_similarity_threshold", "SKILL_TAXONOMY_SIMILARITY_THRESHOLD", 0.92),
    ("skills_extraction._config:embedding_inter_request_delay", "EMBEDDING_REQUEST_DELAY", 0.0),
    ("skills_extraction._config:embedding_input_usd_per_1k_tokens", "EMBEDDING_INPUT_USD_PER_1K_TOKENS", 0.00002),
    # `fifo_fetch_size` reads NORM_BATCH_SIZE legacy env (50 = original FIFO
    # default, distinct from pipeline.normalization.batch_size which is 0).
    ("skills_extraction._config:fifo_fetch_size", "NORM_BATCH_SIZE", 50),
    # ---------- analytics.yaml ----------
    ("analytics._config:staleness_threshold_minutes", "STALENESS_THRESHOLD_MINUTES", 15),
    ("analytics._config:cardinality_cap", "CARDINALITY_CAP", 500),
    ("analytics._config:fresh_threshold_days", "FRESH_THRESHOLD_DAYS", 30),
    ("analytics._config:stale_threshold_days", "STALE_THRESHOLD_DAYS", 90),
    ("analytics._config:disable_minimum_data_guard", "ANALYTICS_DISABLE_MINIMUM_DATA_GUARD", False),
    ("analytics._config:clustering_load_limit", "ANALYTICS_CLUSTERING_LOAD_LIMIT", None),
    ("analytics._config:query_row_limit", "ANALYTICS_QUERY_LIMIT", 100),
    ("analytics._config:query_timeout_seconds", "ANALYTICS_QUERY_TIMEOUT_SECONDS", 30),
    ("analytics._config:qna_live", "ANALYTICS_QNA_LIVE", False),
    ("analytics._config:api_reload", "ANALYTICS_API_RELOAD", True),
    # ---------- laborpulse.yaml ----------
    ("analytics.api._config:confidence_low_below", "LABORPULSE_CONF_LOW_BELOW", 0.60),
    ("analytics.api._config:confidence_high_at_or_above", "LABORPULSE_CONF_HIGH_AT_OR_ABOVE", 0.85),
    ("analytics.api._config:allow_no_api_keys", "LABORPULSE_ALLOW_NO_API_KEYS", False),
    ("analytics.api._config:dashboard_query_mock", "DASHBOARD_ANALYTICS_QUERY_MOCK", True),
    # ---------- ingestion.yaml ----------
    ("ingestion._config:jsearch_country", "JSEARCH_COUNTRY", "us"),
    ("ingestion._config:jsearch_language", "JSEARCH_LANGUAGE", "en"),
    ("ingestion._config:jsearch_date_posted", "JSEARCH_DATE_POSTED", "all"),
    ("ingestion._config:jsearch_start_key_index", "JSEARCH_START_KEY_INDEX", 1),
    ("ingestion._config:jsearch_max_pages", "JSEARCH_MAX_PAGES", 50),
    ("ingestion._config:jsearch_retry_max_retries", "JSEARCH_MAX_RETRIES", 2),
    ("ingestion._config:jsearch_retry_base_delay_seconds", "JSEARCH_RETRY_BASE_DELAY_SECONDS", 10),
    ("ingestion._config:jsearch_retry_max_delay_seconds", "JSEARCH_RETRY_MAX_DELAY_SECONDS", 60),
    ("ingestion._config:jsearch_rps", "JSEARCH_RPS", 5),
    ("ingestion._config:jsearch_rpm", "JSEARCH_RPM", 250),
    ("ingestion._config:scraping_targets", "SCRAPING_TARGETS", []),
    ("ingestion._config:scheduler_type", "SCHEDULER_TYPE", "apscheduler"),
    ("ingestion._config:scheduler_interval_minutes", "INGESTION_INTERVAL_MINUTES", 2),
    ("ingestion._config:scheduler_cron_expression", "INGESTION_CRON_EXPRESSION", None),
    ("ingestion._config:scheduler_state_path", "SCHEDULER_STATE_PATH", None),
    # ---------- eval.yaml ----------
    ("eval._config:extraction_fuzzy_threshold", "EVAL_EXTRACTION_FUZZY_THRESHOLD", 85),
    ("eval._config:qa_latency_sla_seconds", "QA_EVAL_LATENCY_SLA_SECONDS", 45.0),
    ("eval._config:soc_demo_skip_llm", "SOC_DEMO_SKIP_LLM", False),
    ("eval._config:exp004_comparison_csv", "EXP004_COMPARISON_CSV", "data/output/exp004_comparison.csv"),
    ("eval._config:week8_intent_min_accuracy", "WEEK8_INTENT_MIN_ACCURACY", 0.7),
    ("eval._config:week8_intent_max_latency_ms", "WEEK8_INTENT_MAX_LATENCY_MS", 5000.0),
]

# All env vars that the parity test must clear before each subtest, so we
# guarantee the loader resolves from YAML, not env.
_ALL_ENV_VARS: list[str] = sorted({env for _, env, _ in PARITY if env is not None})


def _resolve_callable(spec: str) -> Callable[[], Any]:
    """``module.path:func_name`` → imported callable."""
    module_path, _, func_name = spec.partition(":")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, func_name)


@pytest.fixture
def yaml_only_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete every legacy migrated env var so accessors must read YAML."""
    from common.config_loader import reload_all

    for env in _ALL_ENV_VARS:
        monkeypatch.delenv(env, raising=False)
    reload_all()
    yield
    reload_all()


@pytest.mark.parametrize("spec,env,expected", PARITY, ids=[s for s, _, _ in PARITY])
def test_yaml_value_matches_pre_migration_default(
    spec: str,
    env: str | None,
    expected: Any,
    yaml_only_env: None,
) -> None:
    """Every accessor returns its historical default when env is unset.

    Asserts:
      1. Loader wiring works (no ConfigError, the YAML key exists).
      2. YAML value equals the historical Python-side default.
      3. Reading from YAML, not from .env (env vars deleted in fixture).
    """
    accessor = _resolve_callable(spec)
    actual = accessor()
    if isinstance(expected, float) and isinstance(actual, float):
        assert actual == pytest.approx(expected), (
            f"{spec}: YAML value {actual!r} != historical default {expected!r}. "
            f"Either the YAML drifted or the loader wiring is wrong."
        )
    else:
        assert actual == expected, (
            f"{spec}: YAML value {actual!r} != historical default {expected!r}. "
            f"Either the YAML drifted or the loader wiring is wrong."
        )


def test_every_legacy_env_is_covered_by_parity_table() -> None:
    """The parity table must include every legacy env var declared in the
    no-legacy-reads meta test. Prevents silent drift between the two lists."""
    from tests.test_no_legacy_env_reads import _MIGRATED_VARS

    parity_envs = {env for _, env, _ in PARITY if env is not None}
    # Vars that are tested elsewhere or intentionally not in the parity table:
    #   - llm_costs.yaml entries (16 *_COST_PER_TOKEN) are tested via
    #     test_pricing_loaded_from_yaml_costs below.
    #   - LLM_PROVIDER, LLM_DEFAULT, LLM_SYNTHESIS, LLM_EXTRACTION_*,
    #     LLM_CLASSIFICATION, LLM_ANALYTICS, GEMINI_MODEL,
    #     EXTRACTION_MODEL_TIER are routed through resolve_llm_route /
    #     direct get_str calls in adapter modules — covered by integration
    #     tests in common/tests/test_llm_adapter.py and test_llm_client_async.py.
    skipped = {
        # llm_costs.yaml — covered separately
        "SONNET_INPUT_COST_PER_TOKEN",
        "SONNET_OUTPUT_COST_PER_TOKEN",
        "HAIKU_INPUT_COST_PER_TOKEN",
        "HAIKU_OUTPUT_COST_PER_TOKEN",
        "GPT41_INPUT_COST_PER_TOKEN",
        "GPT41_OUTPUT_COST_PER_TOKEN",
        "GPT41MINI_INPUT_COST_PER_TOKEN",
        "GPT41MINI_OUTPUT_COST_PER_TOKEN",
        "GPT4O_INPUT_COST_PER_TOKEN",
        "GPT4O_OUTPUT_COST_PER_TOKEN",
        "GPT4OMINI_INPUT_COST_PER_TOKEN",
        "GPT4OMINI_OUTPUT_COST_PER_TOKEN",
        "GEMINI_FLASH_INPUT_COST_PER_TOKEN",
        "GEMINI_FLASH_OUTPUT_COST_PER_TOKEN",
        "GEMINI_PRO_INPUT_COST_PER_TOKEN",
        "GEMINI_PRO_OUTPUT_COST_PER_TOKEN",
        # llm.yaml — exercised via resolve_llm_route() and adapter integration tests
        "GEMINI_MODEL",
        "EXTRACTION_MODEL_TIER",
    }
    expected = _MIGRATED_VARS - skipped
    missing = expected - parity_envs
    assert not missing, "Migrated env vars missing from PARITY table:\n  - " + "\n  - ".join(sorted(missing))


def test_pricing_loaded_from_yaml_costs(yaml_only_env: None) -> None:
    """``common/llm_adapter.py:PRICING`` is rebuilt from YAML on import.

    Verifies all eight model entries exist with input/output values that
    match the historical per-token defaults (per-million / 1_000_000).
    """
    import common.llm_adapter
    from common.config_loader import reload_all

    # Force the PRICING table to rebuild from YAML in case test ordering
    # left a stale dict in place from a previous import.
    reload_all()
    pricing = common.llm_adapter._build_pricing()

    expected = {
        "sonnet": (3.00 / 1_000_000, 15.00 / 1_000_000),
        "haiku": (0.25 / 1_000_000, 1.25 / 1_000_000),
        "gpt-4.1-mini": (0.40 / 1_000_000, 1.60 / 1_000_000),
        "gpt-4.1": (2.00 / 1_000_000, 8.00 / 1_000_000),
        "gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
        "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
        "gemini-2.5-flash": (0.15 / 1_000_000, 0.60 / 1_000_000),
        "gemini-2.5-pro": (1.25 / 1_000_000, 10.00 / 1_000_000),
    }
    assert set(pricing.keys()) == set(expected.keys())
    for model, (in_cost, out_cost) in expected.items():
        assert pricing[model]["input"] == pytest.approx(in_cost), (
            f"PRICING[{model}]['input'] = {pricing[model]['input']} != {in_cost}"
        )
        assert pricing[model]["output"] == pytest.approx(out_cost), (
            f"PRICING[{model}]['output'] = {pricing[model]['output']} != {out_cost}"
        )


def test_resolve_llm_route_default_from_yaml(yaml_only_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """``resolve_llm_route()`` returns the YAML-defined provider/deployment when
    no ``LLM_*`` env vars are set."""
    # delete LLM-related vars; yaml_only_env covers the migrated set, but
    # LLM_DEFAULT etc. live outside the parity table.
    for v in (
        "LLM_PROVIDER",
        "LLM_DEFAULT",
        "LLM_SYNTHESIS",
        "LLM_EXTRACTION",
        "LLM_EXTRACTION_TASKS",
        "LLM_EXTRACTION_RESPONSIBILITIES",
        "LLM_EXTRACTION_NAICS",
        "LLM_EXTRACTION_EMPLOYER",
        "LLM_CLASSIFICATION",
        "LLM_ANALYTICS",
    ):
        monkeypatch.delenv(v, raising=False)

    from common.config_loader import reload_all
    from common.llm_adapter import resolve_llm_route

    reload_all()

    provider, deployment = resolve_llm_route(role=None)
    assert provider == "azure_openai"
    assert deployment == "chat-gpt41mini"

    provider, deployment = resolve_llm_route(role="synthesis")
    assert provider == "azure_openai"
    assert deployment == "chat-gpt41"
