# `config/` — YAML configuration files

Non-secret tuning knobs for the Job Intelligence Engine pipeline. Versioned,
diffable, and reviewed in PRs. Secrets and per-environment endpoints stay in
`.env` (which is gitignored).

This convention was adopted via [issue #210](https://github.com/Building-With-Agents/job-intelligence-engine/issues/210).

---

## What lives here

| File | Purpose |
|---|---|
| `pipeline.yaml` | Cross-cutting pipeline knobs: spam thresholds, batch size, DB pool, normalization, sql schema |
| `llm.yaml` | LLM routing roles, Gemini model selection, extraction tier |
| `llm_costs.yaml` | Per-million-token input/output prices for every model |
| `skills_extraction.yaml` | Skills-extraction concurrency, timeouts, taxonomy similarity, embedding cost/delay |
| `enrichment.yaml` | Enrichment concurrency/timeout, dedup thresholds, ESCO seed filter, spam-preview heuristic |
| `clustering.yaml` | HDBSCAN tuning + emergence detection thresholds |
| `analytics.yaml` | Guardrails, query limits, freshness windows, kill-switches |
| `laborpulse.yaml` | LaborPulse (JIE #222–#226): confidence buckets, allow-no-keys dev escape, dashboard mock |
| `ingestion.yaml` | JSearch tuning, scheduler interval/cron, scraping targets |
| `eval.yaml` | Eval-only knobs: extraction fuzzy threshold, QA SLA, SOC demo, exp004, week8 intent |
| `ingestion_queries.yaml` | (Pre-existing) JSearch query catalog with monthly budget |

---

## What does NOT live here

- **Secrets** (API keys, DB DSNs, auth tokens) — stay in `.env`. Examples:
  `AZURE_OPENAI_API_KEY`, `PYTHON_DATABASE_URL`, `JIE_API_KEYS`, `JSEARCH_API_KEY*`,
  `LANGFUSE_SECRET_KEY`, `GEMINI_API_KEY`, `ANALYTICS_QUERY_X_API_KEY`.
- **Per-environment endpoints** (hostnames, deployment names, port numbers) — stay in `.env`.
  Examples: `AZURE_OPENAI_ENDPOINT`, `LANGFUSE_BASE_URL`, `REDIS_URL`,
  `ANALYTICS_API_HOST`, `ANALYTICS_API_PORT`, `AZURE_OPENAI_API_VERSION`.
- **Per-tenant data** (allowlists, region scopes) — stay in `.env` for now.
  `JIE_TENANT_ALLOWLIST` may move to `config/tenants.yaml` when multi-tenancy expands.

---

## Loader contract

All config reads go through [`common.config_loader`](../common/config_loader.py).
Public API:

| Function | Purpose |
|---|---|
| `load_yaml(name)` | Load and cache `config/<name>.yaml`; missing files return `{}`. |
| `get_int(*, file, key, env, default, minimum=None, maximum=None)` | Typed int read; out-of-range or parse error → default. |
| `get_float(...)` | Typed float read; same range/error semantics. |
| `get_bool(*, file, key, env, default)` | Accepts `1/true/yes/on/0/false/no/off` (case-insensitive). |
| `get_str(*, file, key, env, default)` | Typed string read with default. |
| `get_optional_str(*, file, key, env)` | Typed optional string — returns `None` when missing. |
| `get_list(*, file, key, env, default, separator=",")` | Reads YAML list or CSV string. |
| `cached_accessor(fn)` | Decorator: `lru_cache(maxsize=1)` + register for `reload_all()`. |
| `reload_all()` | Test-only: clear all caches. |

**Resolution order per accessor:**
1. If `env=` is set AND the named env var is set → env wins, log
   `structlog.warning("config_env_override_used", ...)` once.
2. Otherwise resolve YAML by dotted key.
3. Otherwise return `default`.

Out-of-range / parse errors return the default (preserves existing
`analytics/clustering/config.py` semantics).

---

## Env-override convention

**Every YAML leaf carries a `# Env override: VAR_NAME` comment** naming the
legacy env var it migrated from. Example:

```yaml
pipeline:
  spam:
    # Heuristic spam score above which jobs are flagged for operator review.
    # Range: 0.0–1.0.
    # Env override: SPAM_FLAG_THRESHOLD
    flag_threshold: 0.7
```

To find the env var for any value: read the comment in the YAML.
To find the YAML key for any env var: `grep "Env override: VAR_NAME" config/`.

The env override is a transitional safety net. It will be removed in a
follow-up PR after 1–2 release cycles (see issue #210). Until then, the
loader emits a one-shot `structlog.warning` per (env, file, key) tuple so ops
can spot deployments still relying on env overrides.

---

## Adding a new tuning variable

1. Decide which file the var belongs to (use the table above).
   Create a new file only with reviewer approval.
2. Add the leaf with a 1–2-line docstring AND a `# Env override: NEW_VAR`
   comment.
3. Add a typed accessor in the subsystem's `_config.py` calling
   `cached_accessor`. Pass `minimum=`/`maximum=` if there's a valid range.
4. Read it from code via the accessor — never `os.getenv` directly.
5. If a default range is enforced, document min/max in the YAML comment.

## Removing a deprecated variable

1. Remove the leaf from YAML and the accessor from `_config.py`.
2. Remove all call sites.
3. Remove the env var from `.env.example`.
4. Note the removal in `CHANGELOG.md`.

---

## Reload semantics (tests only)

`common.config_loader.reload_all()` clears the YAML cache and every registered
accessor cache. Use in pytest fixtures only (see
`common/tests/test_config_loader.py` for the `tmp_config_dir` pattern).

Production callers must not call `reload_all` — config is process-scoped
immutable.

---

## Migration notes

- **Pruned from `.env.example`:** `INGESTION_SCHEDULE` (use
  `INGESTION_CRON_EXPRESSION`), `LANGFUSE_HOST` (code reads `LANGFUSE_BASE_URL`),
  `LANGSMITH_API_KEY` (project chose Langfuse via ADR-006).
- `llm_costs.yaml` replaces 16 `*_COST_PER_TOKEN` env vars — vendor pricing
  now lives in YAML; env vars remain as emergency overrides only.
- See `CHANGELOG.md` for the full migration entry.
