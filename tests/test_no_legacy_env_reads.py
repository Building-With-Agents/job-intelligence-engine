"""Meta-test: production code must not call ``os.getenv("VAR")`` directly for any
migrated PUBLIC-TUNING var. Use the typed accessor in ``<subsystem>/_config.py``
instead.

Allowlisted call sites: ``*/_config.py`` modules (the accessors), test files,
``common/config_loader.py`` itself, and a small set of intentional leaves
(documented inline). See GitHub issue #210.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Variables that have been migrated to YAML accessors. Every one of these
# should be unreachable from non-test, non-_config.py code via direct os.getenv.
_MIGRATED_VARS: set[str] = {
    # pipeline.yaml
    "BATCH_SIZE",
    "SPAM_FLAG_THRESHOLD",
    "SPAM_REJECT_THRESHOLD",
    "SKILL_CONFIDENCE_THRESHOLD",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "JIE_SQL_SCHEMA",
    "NORM_BATCH_SIZE",
    # enrichment.yaml
    "ENRICHMENT_PARALLEL",
    "ENRICHMENT_CONCURRENCY",
    "ENRICHMENT_LLM_TIMEOUT",
    "DEDUP_COSINE_THRESHOLD",
    "DEDUP_ROLLING_WINDOW_DAYS",
    "SPAM_PREVIEW_ALLOW_HEURISTIC",
    "ESCO_SEED_APPLY_FILTER",
    # skills_extraction.yaml
    "SKILLS_EXTRACTION_PARALLEL",
    "SKILLS_EXTRACTION_CONCURRENCY",
    "SKILLS_EXTRACTION_CHUNK_SIZE",
    "SKILLS_EXTRACTION_CHUNK_COOLDOWN",
    "SKILLS_EXTRACTION_DELAY",
    "SKILLS_EXTRACTION_LLM_TIMEOUT",
    "SKILLS_EXTRACTION_MAX_JOBS",
    "SKILL_TAXONOMY_SIMILARITY_THRESHOLD",
    "EMBEDDING_REQUEST_DELAY",
    "EMBEDDING_INPUT_USD_PER_1K_TOKENS",
    # clustering.yaml
    "CLUSTER_EMBEDDING_BATCH_SIZE",
    "CLUSTER_EMBEDDING_AUDIT_AGENT_NAME",
    "CLUSTER_MIN_TOTAL_POSTINGS",
    "CLUSTER_MIN_CLUSTER_SIZE",
    "CLUSTER_MIN_SAMPLES",
    "CLUSTER_SELECTION_EPSILON",
    "CLUSTER_DISTANCE_METRIC",
    "CLUSTER_LABEL_DOMINANCE_THRESHOLD",
    "EMERGENCE_MIN_QUALITY_SCORE",
    "EMERGENCE_MIN_NOVEL_SKILLS",
    "EMERGENCE_MIN_DISTINCT_EMPLOYERS",
    # analytics.yaml
    "STALENESS_THRESHOLD_MINUTES",
    "CARDINALITY_CAP",
    "FRESH_THRESHOLD_DAYS",
    "STALE_THRESHOLD_DAYS",
    "ANALYTICS_DISABLE_MINIMUM_DATA_GUARD",
    "ANALYTICS_CLUSTERING_LOAD_LIMIT",
    "ANALYTICS_QUERY_LIMIT",
    "ANALYTICS_QUERY_TIMEOUT_SECONDS",
    "ANALYTICS_QNA_LIVE",
    "ANALYTICS_API_RELOAD",
    # laborpulse.yaml
    "LABORPULSE_CONF_LOW_BELOW",
    "LABORPULSE_CONF_HIGH_AT_OR_ABOVE",
    "LABORPULSE_ALLOW_NO_API_KEYS",
    "DASHBOARD_ANALYTICS_QUERY_MOCK",
    # ingestion.yaml
    "JSEARCH_COUNTRY",
    "JSEARCH_LANGUAGE",
    "JSEARCH_DATE_POSTED",
    "JSEARCH_MAX_PAGES",
    "JSEARCH_MAX_RETRIES",
    "JSEARCH_RETRY_BASE_DELAY_SECONDS",
    "JSEARCH_RETRY_MAX_DELAY_SECONDS",
    "JSEARCH_RPS",
    "JSEARCH_RPM",
    "INGESTION_INTERVAL_MINUTES",
    "INGESTION_CRON_EXPRESSION",
    "SCHEDULER_TYPE",
    "SCHEDULER_STATE_PATH",
    # eval.yaml
    "EVAL_EXTRACTION_FUZZY_THRESHOLD",
    "EXP004_COMPARISON_CSV",
    "SOC_DEMO_SKIP_LLM",
    "QA_EVAL_ANSWERABILITY_GATE_THRESHOLD",
    # llm.yaml
    "GEMINI_MODEL",
    "EXTRACTION_MODEL_TIER",
    # #279: LLM role tier env vars are read via common.llm_adapter.resolve_llm_route()
    # so per-call routing overrides (e.g. LLM_SYNTHESIS=gemini:gemini-2.5-pro) are
    # honored. Direct os.getenv reads bypass provider-prefix parsing.
    "LLM_DEFAULT",
    "LLM_SYNTHESIS",
    # llm_costs.yaml
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
}

# Files / directories where direct os.getenv reads are allowed.
_ALLOWED_PATH_FRAGMENTS = (
    "/_config.py",
    "\\_config.py",
    "/pipeline_config.py",
    "\\pipeline_config.py",
    "/config_loader.py",
    "\\config_loader.py",
    "/common/llm_adapter.py",  # _per_token_from_yaml uses os.getenv to detect legacy env presence
    "\\common\\llm_adapter.py",
    # #279: standalone diagnostic CLIs that intentionally inspect raw env to display
    # what's configured (presence check, not LLM routing). Not a production code path.
    "/run_soc_demo.py",
    "\\run_soc_demo.py",
    "/scripts/test_llm_connection.py",
    "\\scripts\\test_llm_connection.py",
    "/tests/",
    "\\tests\\",
    "test_",
)

_GETENV_RE = re.compile(r"""os\.getenv\(\s*['"]([A-Z][A-Z0-9_]*)['"]""")


def _is_allowed_file(path: Path) -> bool:
    s = str(path)
    return any(frag in s for frag in _ALLOWED_PATH_FRAGMENTS)


def test_no_direct_legacy_env_reads_in_production_code() -> None:
    """Walk all .py files under the repo root; assert no migrated var name is read
    via direct ``os.getenv`` outside the allowlisted paths."""
    py_files = [p for p in _REPO_ROOT.rglob("*.py") if ".venv" not in str(p) and "__pycache__" not in str(p)]
    violations: list[tuple[str, int, str]] = []
    for path in py_files:
        if _is_allowed_file(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _GETENV_RE.finditer(line):
                var = match.group(1)
                if var in _MIGRATED_VARS:
                    rel = path.relative_to(_REPO_ROOT)
                    violations.append((str(rel), lineno, var))
    assert not violations, (
        "Direct os.getenv reads found for migrated config vars (move call to a "
        "_config.py accessor or extend _ALLOWED_PATH_FRAGMENTS):\n  "
        + "\n  ".join(f"{f}:{lineno} reads {var}" for f, lineno, var in violations)
    )
