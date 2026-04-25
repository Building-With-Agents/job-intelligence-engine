# Week 08 — Workforce Q&A + Disruption Fingerprints Testing Runbook

End-to-end testing guide for the Week 8 deliverables: disruption fingerprints refresh (Pair A), workforce-question Q&A pipeline (Pairs B + C — intent → router → evidence → synthesis → follow-ups with cost tracking), FastAPI analytics layer + on-demand triggers (Pair D), SQL guardrails, audit logging, and the new Streamlit pages (Ask the Data, Skills Gap Map, Emergence Alerts, Regional Heatmap).

This runbook **extends** [WEEK07_TESTING_RUNBOOK.md](WEEK07_TESTING_RUNBOOK.md). Week 8 does not replace the upstream pipeline — it reads from the same aggregate tables that Week 7 produces. Refer to the Week 7 runbook for ingestion, normalization, skills extraction, enrichment, and analytics aggregate verification.

All commands assume you are at the **repo root** with the Python venv activated.

### Activate the venv first

`cd` into your local clone of `job-intelligence-engine`, then activate the venv.

**Windows (PowerShell):**

```powershell
cd <path-to-repo>\job-intelligence-engine
.venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
cd <path-to-repo>/job-intelligence-engine
source .venv/bin/activate
```

### Imports resolve without PYTHONPATH when running `python -c` from the repo root

`python -c "..."` automatically prepends the current working directory to `sys.path` (it sets `sys.path[0] = ""`), so `analytics.*` / `common.*` / `api.*` imports resolve without any extra setup **as long as you run from the repo root**. Verify:

```
python -c "from analytics.query_engine.qna import run_analytics_qna; print('ok')"
```

Expect: `ok`.

### On the `PYTHONPATH=. python ...` prefix that appears later in this runbook

Many commands below include the `PYTHONPATH=.` prefix out of habit. It's harmless on bash/zsh/git-bash and redundant from the repo root. In **PowerShell**, the inline prefix form does not work — PowerShell parses `PYTHONPATH=.` as a command name and errors out.

- **PowerShell users:** drop the `PYTHONPATH=.` prefix from the commands below; run just `python ...`. Works from the repo root because of the `sys.path[0] = ""` behavior above.
- **Bash users:** leave the prefix as-is, or drop it; it's functionally identical from the repo root.

If you need to run from a subdirectory (not the repo root), set `$env:PYTHONPATH = "."` (PowerShell) or `export PYTHONPATH=/path/to/repo-root` (bash) once at the start of your session.

---

## Table of Contents

0. [Quick Start — Verifying Week 8 (Q&A + Disruption)](#0-quick-start--verifying-week-8-qa--disruption)
1. [Overview — What Week 8 Adds](#1-overview--what-week-8-adds)
2. [Environment Setup](#2-environment-setup)
3. [Upstream Data (Week 6–7 Prerequisites)](#3-upstream-data-week-67-prerequisites)
4. [Disruption Fingerprints Pipeline (Pair A)](#4-disruption-fingerprints-pipeline-pair-a)
5. [Q&A Pipeline End-to-End (Pairs B + C)](#5-qa-pipeline-end-to-end-pairs-b--c)
6. [FastAPI API Smoke Test (Pair D)](#6-fastapi-api-smoke-test-pair-d)
7. [SQL Guardrails — Adversarial Verification](#7-sql-guardrails--adversarial-verification)
8. [Audit Log Verification](#8-audit-log-verification)
9. [Streamlit Dashboard — New Pages](#9-streamlit-dashboard--new-pages)
10. [Cost Tracking](#10-cost-tracking)
11. [Troubleshooting](#11-troubleshooting)

---

## 0. Quick Start — Verifying Week 8 (Q&A + Disruption)

**If data has already been seeded and the Week 7 aggregates are populated, use this happy-path to confirm Week 8 works end-to-end in under five minutes.** This section assumes you pulled `development` after all four Week 8 PRs merged (#137 Pair A, #153 Pair B, #154 Pair C, #161 Pair D).

### Prerequisites

1. `.env` has `PYTHON_DATABASE_URL`, `LLM_DEFAULT=chat-gpt41mini`, `LLM_SYNTHESIS=chat-gpt41`, and Azure OpenAI credentials.
2. Venv activated, `pip install -r requirements.txt` is current.
3. Database is reachable: `python scripts/db_check.py tables`.
4. Week 7 aggregate tables are populated (if empty, see §9 Prerequisite Steps A–C to populate them).

### Step 1 — Confirm upstream data and aggregates are present

```
python scripts/week8_verify_counts.py
```

This emits a three-section report (Week 6 / Week 7 / Week 8) with row counts and a `(empty)` / `MISSING` marker per table.

**Minimum for Week 8 verification:**

| Table | Minimum Expected | Why |
|-------|-----------------|-----|
| job_postings | 1,500+ | Source for aggregate + disruption |
| skill_demand_weekly | 100+ | Required for Ask-the-Data and Emerging Skills trigger |
| role_snapshot_weekly | 10+ | Required for role_benchmark trigger |
| canonical_roles | 5+ | Required for disruption fingerprints |
| skill_velocity | 50+ | Required for Emergence Alerts page |

If `canonical_roles` or any Week 7 aggregate is `(empty)`, run the Pair C clustering flow (Step 2 below) and the aggregate population commands in §9 Prerequisite (Steps A–C) before continuing.

If any Week 8 table shows `MISSING`, run migrations per §2 above.

### Step 2 — If `canonical_roles` is empty, run clustering first (Pair C flow)

The disruption pipeline iterates over rows in `dbo.canonical_roles`. On a fresh dev DB this table starts empty, so Step 3 will return `roles_considered=0` until clustering runs.

Run Pair C's clustering pipeline to populate it:

```
python scripts/run_clustering.py
```

**What this does:**

1. Loads every `job_postings` row that has extracted skills/tools/responsibilities from `dbo.extracted_intelligence`.
2. Generates an embedding vector per posting via Azure OpenAI's `embeddings-te3small` deployment.
3. Runs HDBSCAN clustering over the embeddings to group postings into canonical roles.
4. Labels each cluster via a cascade: dominant title → LLM fallback (`LLM_DEFAULT`, Haiku-class) → most-common title string.
5. Detects **Emergence candidates** — noise postings with novel skills from multiple employers that look like a new-but-undersized role.
6. Persists the result: inserts rows into `dbo.canonical_roles`, updates `job_postings.canonical_role_id` FK, refreshes `dbo.role_snapshot_weekly` for the current ISO week.

**Cost:** ~1 embedding + 0–1 label LLM call per unique cluster. At ~3,500 postings forming ~5–15 clusters, the run costs roughly $0.05–$0.15 total.

**Knobs:**
- `--min-postings N` — raise the minimum posting threshold before clustering will run (default from `CLUSTER_MIN_TOTAL_POSTINGS` env, typically 500).
- If you have fewer than 500 postings, export `CLUSTER_MIN_TOTAL_POSTINGS=20` for testing.

**Expected output (abbreviated):**
```
CANONICAL ROLE CLUSTERING — LIVE DATA RUN
Features loaded:  3138
With skills:      2841 (90.5%)
--- Generating embeddings (Azure OpenAI) ---
Embeddings count: 3138
--- Running HDBSCAN clustering ---
Clustering results:
  Total input:        3138
  Eligible:           2920
  Clusters found:     8
  Noise postings:     1863
  Noise rate:         63.8%
--- Top clusters ---
   1. [412 posts] Full Stack Developer
       Skills: Python, React, SQL, ...
   ...
--- Persisting to DB ---
  Roles inserted:    8
  Postings updated:  1057
```

Verify:

```
python scripts/db_check.py query "SELECT COUNT(*) AS n FROM dbo.canonical_roles"
```

### Step 3 — Refresh disruption fingerprints once (Pair A)

```
python scripts/smoke/disruption_refresh.py
```

**Expected (with `canonical_roles` populated):**
```
roles_considered:    <N>
computed_count:      <N>
refresh_duration_ms: <ms>
```

**Expected (empty `canonical_roles`):**
```
roles_considered:    0
computed_count:      0

NOTE: canonical_roles is empty. Run Pair C's clustering flow first ...
```
If you see the empty case, run Step 2 above before re-running this script.

Confirm rows landed:

```
python scripts/db_check.py query "SELECT COUNT(*) AS n FROM dbo.disruption_fingerprints"
```

### Step 4 — Run one Q&A through the full pipeline

```
python scripts/smoke/qa_pipeline.py
```

**Optional flags** (default uses a canonical skills question and auto-generates a correlation id):

```
python scripts/smoke/qa_pipeline.py --question "Which regions pay the most for data analysts?"
python scripts/smoke/qa_pipeline.py --correlation-id wk8-demo-1
python scripts/smoke/qa_pipeline.py --answer-preview-chars 500
```

**What the script does** (core body, for reference — same code is in `scripts/smoke/qa_pipeline.py`):

```python
from analytics.query_engine.routing import run_guardrailed_analytics_query
from common.data_store.database import session_scope
from common.types.query_request import QueryRequest

req = QueryRequest(query="What are the top 5 skills by posting count across all weeks?")
with session_scope() as session:
    resp = run_guardrailed_analytics_query(
        req,
        session=session,
        correlation_id="qa-smoke-0",
    )

# resp is a SynthesisResponse pydantic model with these fields:
# - answer_text            (str; empty on refused=True)
# - citations              (list[EvidenceCitation])
# - confidence             (float 0.0-1.0) + confidence_flagged_low + confidence_explanation
# - volume_flagged_low     (bool) + volume_warning
# - refused                (bool) + refusal_message
# - follow_up_questions    (list[str], 2-3 entries)
# - total_cost_usd         (float)
# - cost_breakdown_usd     (dict[str, float]; keys: intent_classification, sql_generation,
#                           synthesis, follow_up)
```

Under the hood, `run_guardrailed_analytics_query` fires every leg of the Week 8 pipeline:

1. **Intent classification** — `complete(role="classification")` -> `LLM_DEFAULT` (Haiku-tier gpt-4.1-mini)
2. **SQL generation** — `complete(role="analytics")` with the intent label + schema hint
3. **SQL guardrails** — `sql_guardrails.validate_sql()` rejects non-SELECT, off-allowlist, multi-statement, etc.
4. **Safe execute** — `execute_safe.execute_validated_query()` applies 30s `statement_timeout` at the Postgres session level
5. **Evidence bundle** — `evidence.build_evidence_bundle()` extracts citations from the rows (deterministic; no LLM)
6. **Synthesis** — `synthesize_answer()` -> `complete(role="synthesis")` -> `LLM_SYNTHESIS` (Sonnet-tier gpt-4.1). Skipped entirely on `bundle.refuse_synthesis=True`.
7. **Follow-ups** — Haiku-class generation of 2–3 related questions
8. **Audit log** — every call writes a row to `dbo.orchestration_audit_log` with correlation_id, question_hash, sql_hash

**Expected output (happy path):**

- `answer` is a 1–3 sentence paragraph naming real skills with posting counts.
- `citations` is a non-zero integer (usually 3–10).
- `confidence` between 0.0 and 1.0 (typically 0.55–0.85 on real aggregate data).
- `cost_breakdown_usd` contains keys `intent_classification`, `sql_generation`, `synthesis`, `follow_up`.

**Expected output (NO_DATA refusal — common when Week 7 aggregates are empty):**

- `refused=True`, `answer_text=""`, `refusal_message="No data in scope for the selected filters."`
- `cost_breakdown_usd` contains **only** `intent_classification` (synthesis is skipped when refused).
- `citations=0`, `follow_up_questions=0`.
- This is the correct safe behavior — no LLM fabrication when the data isn't there.

### Step 5 — Confirm the audit row was written

```bash
python scripts/db_check.py query "SELECT endpoint, success, confidence, correlation_id FROM dbo.orchestration_audit_log ORDER BY created_at DESC LIMIT 3"
```

**Expected:** Most recent row has `endpoint='/analytics/query'`, `success=true`, and the `correlation_id` matches what you passed (`qa-smoke-0`).

If these five steps pass, Week 8 core is wired end-to-end. Continue below for deeper per-pair verification, API smoke, adversarial SQL, and Streamlit page walkthroughs.

---

## 1. Overview — What Week 8 Adds

Week 8 layers four new capabilities on top of the Week 7 analytics aggregates:

### 1.1 Disruption fingerprints pipeline (Pair A)

`analytics/disruption/` introduces a disruption classifier that reads canonical roles + temporal period snapshots (pre-GPT, early-GenAI, post-GPT-4, agentic era), computes per-role signals (skill velocity, tool transition, task shift, responsibility expansion, AI intensity trend, workflow restructuring score), assigns multi-label disruption categories (Displacement / Augmentation / Transformation / Emergence), and writes fingerprints with a deterministic content hash to `dbo.disruption_fingerprints`. The service emits a `DisruptionRefreshed` event (via `build_disruption_refreshed_envelope`) when a bus is registered.

### 1.2 Workforce Q&A pipeline (Pairs B + C)

`analytics/query_engine/` implements the Ask-the-Data flow end-to-end:

| Module | Responsibility |
|--------|----------------|
| `intent.py` | `classify_workforce_question()` — 10-way intent classifier with entity extraction. Haiku-tier via `role="classification"` (Azure OpenAI `chat-gpt41mini` via `LLM_DEFAULT`) |
| `router.py` | `QueryRouter` — intent → SQL payload mapping for ORM triggers |
| `routing.py` | `run_guardrailed_analytics_query()` — the NL→SQL path: intent classify, generate SQL, validate, execute under 30s timeout, hand off to evidence + synthesis |
| `sql_guardrails.py` | `validate_ask_the_data_sql()` (regex, operational `dbo.*` tables) and `validate_sql()` (sqlglot, aggregate tables) |
| `execute_safe.py` | `execute_validated_query()` — enforces `SET LOCAL statement_timeout = 30000` |
| `evidence.py` | `build_evidence_bundle()` — converts query rows into `EvidenceCitation` list with sufficiency, period coverage, blended confidence, refusal policy |
| `synthesis.py` | `synthesize_answer()` — Sonnet-tier grounded narrative + follow-up Haiku-tier questions, with anti-hallucination retry |
| `qna.py` | Thin orchestration: evidence → synthesis |
| `audit_log.py` | Appends SQL validation rows to `dbo.llm_audit_log` and Q&A outcomes to `dbo.orchestration_audit_log` |
| `ledger_utils.py` | Populates `CostLedger` per leg (intent_classification, sql_generation, synthesis, follow_up) |

### 1.3 FastAPI layer + on-demand triggers (Pair D)

`analytics/api/app.py` exposes a FastAPI app with five endpoints:

| Endpoint | Purpose |
|----------|---------|
| `POST /analytics/query` | Natural-language Q&A through `run_guardrailed_analytics_query` |
| `POST /analytics/triggers/cohort_gap_analysis` | Top 50 skills for a cohort/week, 24h cached |
| `POST /analytics/triggers/role_benchmark` | Role snapshots for a canonical role id |
| `POST /analytics/triggers/emerging_skills_scan` | Velocity scan with min-posting-count filter |
| `POST /analytics/triggers/custom_employer_comparison` | Employer context from `sector_summary_weekly` |

Triggers cache into `dbo.cohort_gap_cache` with a 24-hour TTL, keyed by `(trigger_type, request_hash)`. OpenAPI docs are served at `/docs` and `/redoc`.

### 1.4 New Streamlit pages (Pairs C + D)

`dashboard/streamlit_app.py` registers four new pages:

| Page | Module | Consumes |
|------|--------|----------|
| Ask the Data | `pages_ask_the_data.py` | `/analytics/query` (live chat with streaming, confidence banners, follow-up chips) |
| Skills Gap Map | `pages_skills_gap_map.py` | `dbo.cohort_gap_cache` |
| Emergence Alerts | `pages_emergence_alerts.py` | `dbo.disruption_fingerprints` + `dbo.canonical_roles` |
| Regional Heatmap | `pages_regional_heatmap.py` | `dbo.geo_demand_weekly` |

```
[Week 7 aggregates]
     ↓
[Week 8 additions]
     ├── Pair A: Disruption Fingerprints  → dbo.disruption_fingerprints
     ├── Pair B: Intent classifier + router (Haiku-tier)
     ├── Pair C: Evidence bundler + synthesis (Sonnet-tier) + Ask-the-Data UI
     └── Pair D: FastAPI + 4 triggers + SQL guardrails + audit log + 3 viz pages
```

---

## 2. Environment Setup

Week 8 builds on Week 7's environment. Only the additions below are new.

### New `.env` additions for Week 8

```bash
# LLM routing (PR #155). LLM_DEFAULT is Haiku-tier; LLM_SYNTHESIS is Sonnet-tier.
LLM_PROVIDER=azure_openai
LLM_DEFAULT=chat-gpt41mini
LLM_SYNTHESIS=chat-gpt41

# Optional per-role overrides — only set if you want to route a specific role off the defaults.
# LLM_CLASSIFICATION=chat-gpt41mini          # Intent classifier (already covered by LLM_DEFAULT)
# LLM_ANALYTICS=chat-gpt41mini               # NL→SQL generation (already covered by LLM_DEFAULT)

# Azure credentials (same as Week 6/7)
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_API_VERSION=2024-08-01-preview

# Analytics API host/port (defaults shown)
ANALYTICS_API_HOST=127.0.0.1
ANALYTICS_API_PORT=8000
ANALYTICS_API_RELOAD=1
```

### Rate limits worth knowing before the demo

Azure's `chat-gpt41` (Sonnet-tier) deployment is **5 requests/min and 5K tokens/min** on our contract. Every Q&A run burns one synthesis call + one follow-up call (both on `chat-gpt41`). Back-to-back demo runs will hit the rate ceiling. If you plan to run the API against `chat-gpt41` during a live demo, stagger calls at least 20 seconds apart or pre-warm the cache via a dry run.

`chat-gpt41mini` (Haiku-tier) has much higher throughput and handles intent classification + SQL generation + follow-ups without contention.

### Why both `LLM_DEFAULT` and `LLM_SYNTHESIS` must be set

The adapter in `common/llm_adapter.py` resolves routes via:

1. `LLM_{ROLE}` env var (e.g. `LLM_SYNTHESIS` for `role="synthesis"`)
2. `LLM_DEFAULT` env var
3. **ValueError** — no legacy fallback

If `LLM_DEFAULT` is unset you will see `ValueError: No LLM deployment configured. Set LLM_DEFAULT or LLM_{ROLE} in your .env.` on the very first intent-classification call. Copy the LLM block from `.env.example` rather than composing it by hand.

### Install Week 8 dependencies

PR #161 (Pair D) adds `sqlglot`, `fastapi`, and `uvicorn[standard]` as hard deps. If your venv was created before the merge, update it:

```bash
pip install -r requirements.txt
# or targeted: pip install 'sqlglot>=25.0' 'fastapi>=0.115' 'uvicorn[standard]>=0.30'
```

Confirm imports succeed:
```bash
python -c "import sqlglot, fastapi, uvicorn; print('ok')"
```

If you skip this, `from analytics.query_engine.sql_guardrails import validate_sql` fails with `ModuleNotFoundError: No module named 'sqlglot'` before any runbook command can run.

### Run Week 8 migrations

PR #161 adds two new tables (`cohort_gap_cache`, `orchestration_audit_log`) and re-runs a canonical-role FK. Apply the migrations once:

```bash
python -c "
from dotenv import load_dotenv; load_dotenv()
from common.data_store.database import get_engine
from common.data_store.migrations import run_migrations
run_migrations(get_engine())
"
```

**Expected warnings (both benign):**
- `migration_orchestration_audit_skipped error='... column "event_type" does not exist ...'` — `migrations.py` line 513 references a column name that doesn't exist on the model (`endpoint` is the actual column). Index is not created; rows still write fine. See Troubleshooting §11 for the follow-up fix.
- `migration_canonical_role_fk_skipped error='... constraint "fk_job_postings_canonical_roles" ... already exists'` — idempotent re-run; safe to ignore.

### Start Docker services (unchanged from Week 7)

```bash
docker compose up -d
docker compose ps   # expect Postgres + the 6 Langfuse containers running
```

### Verify database connectivity

```
python scripts/db_check.py tables
```

For row counts across Week 6 / 7 / 8 expected tables (with `MISSING` for tables that don't exist yet), use the dedicated script:

```
python scripts/week8_verify_counts.py
```

This replaces `db_check.py counts` for Week 8 purposes — `db_check.py counts` only reports 8 upstream tables (a hardcoded UNION ALL), it does not know about Week 7 aggregates, Week 8 Q&A, or disruption tables.

---

## 3. Upstream Data (Week 6–7 Prerequisites)

Week 8 does **not** re-run the ingestion → normalization → extraction → enrichment → analytics pipeline. It reads the Week 7 aggregate tables. If any of those are empty, Week 8 will surface them as "sparse" or "no_data" at the synthesis layer rather than failing loudly — this is by design, but it produces a bad demo.

### Minimum tables and row counts

| Table | Minimum | Consumed by | See |
|-------|---------|-------------|-----|
| `dbo.job_postings` | 1,500+ | Ask-the-Data SQL path | Week 7 §4 |
| `dbo.skill_demand_weekly` | 100+ | Ask-the-Data, cohort_gap_analysis trigger | Week 7 §5 |
| `dbo.tool_demand_weekly` | 50+ | Ask-the-Data | Week 7 §5 |
| `dbo.role_snapshot_weekly` | 10+ | role_benchmark trigger | Week 7 §5 |
| `dbo.sector_summary_weekly` | 5+ | custom_employer_comparison trigger | Week 7 §5 |
| `dbo.geo_demand_weekly` | 10+ | Regional Heatmap page | Week 7 §5 |
| `dbo.skill_velocity` | 50+ | emerging_skills_scan trigger, Emergence Alerts page | Week 7 §5 |
| `dbo.canonical_roles` | 5+ | Disruption fingerprints | Week 7 Pair C |
| `dbo.orchestration_audit_log` | (empty ok) | Populated by Week 8 Q&A | Week 8 §8 |
| `dbo.cohort_gap_cache` | (empty ok) | Populated by Week 8 triggers | Week 8 §9 |
| `dbo.disruption_fingerprints` | (empty ok) | Populated by Week 8 Pair A | Week 8 §4 |

### One-shot verification

```
python scripts/week8_verify_counts.py
```

**Expected output** is a three-section report (Week 6 / Week 7 / Week 8) with one row per expected table showing `count`, expected-row-count hint, and a `(empty)` or `MISSING` marker when applicable. Anything marked MISSING under Week 8 means the PR #161 migrations have not run locally yet — see §2 "Run Week 8 migrations".

If any required table is 0 under Week 7, refresh the Week 7 aggregates for an anchor Monday (see WEEK07_TESTING_RUNBOOK.md Section 5 for the canonical Pair A flow).

---

## 4. Disruption Fingerprints Pipeline (Pair A)

The disruption service orchestrates: load canonical roles → fetch per-role temporal snapshots → normalize across the 4 locked periods → compute signals → classify categories → compute content fingerprint → persist → emit `DisruptionRefreshed`.

### Step 1 — Confirm the table exists and is reachable

```bash
python scripts/db_check.py query "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='dbo' AND table_name='disruption_fingerprints' ORDER BY ordinal_position"
```

**Expected columns:** `canonical_role_id`, `disruption_category` (JSONB), `disruption_intensity`, `skill_velocity` (JSONB), `tool_transition` (JSONB), `task_shift` (JSONB), `responsibility_expansion`, `ai_intensity_trend`, `workflow_restructuring_score`, `trajectory`, `period_comparison` (JSONB), `content_fingerprint`, `computed_at`.

If the table does not exist, the migration in `common/data_store/migrations.py` for issue #108 has not run yet:

```bash
python scripts/db_check.py migrate
```

### Step 2 — Run a full refresh (verbose — prints first 3 fingerprints)

```
python scripts/smoke/disruption_refresh.py --verbose
```

**What the script does** (core body — same code in `scripts/smoke/disruption_refresh.py`):

```python
from analytics.disruption.service import DisruptionFingerprintService
from common.data_store.database import session_scope

svc = DisruptionFingerprintService()
with session_scope() as session:
    result = svc.refresh_disruption_fingerprints(
        session=session,
        correlation_id="wk8-disruption-demo",
    )
# result.fingerprints is a list of persisted DisruptionFingerprintRecord objects
# Each has: canonical_role_id, disruption_category (list[str]),
#           ai_intensity_trend (str), workflow_restructuring_score (float 0-1)
```

**Expected output:**

- `roles_considered` equals the row count in `dbo.canonical_roles`.
- `computed_count` equals `roles_considered` (every role gets a fingerprint row, including sparse ones).
- For each printed role: `cats` is a list of disruption patterns (Displacement / Augmentation / Transformation / Emergence — a role may carry multiple labels); `ai_trend` is one of `increasing`/`decreasing`/`stable`; `wrs` is a float in `[0, 1]`.

**If `cats=[]` on every role:** This is expected when all postings come from a single temporal period (e.g., all recent JSearch ingestions land in `agentic_era`). The classifier compares skill/tool/task mix **across** `pre_chatgpt` → `early_genai` → `post_gpt4` → `agentic_era`. With only one period of data, all period-over-period deltas are zero, the 30% skill-change threshold is never met, and no patterns fire. The classifier is working correctly — it doesn't fabricate patterns when the data doesn't support them. To see non-empty categories, seed test data across multiple temporal periods or backfill `temporal_period` on historical postings.

**Optional flags:**
- `--show 5` — print the first 5 fingerprints instead of 3
- `--correlation-id my-demo-1` — use a specific correlation id for tracing

### Step 3 — Verify rows landed with the expected shape

```bash
python scripts/db_check.py query "SELECT canonical_role_id, disruption_category, ai_intensity_trend, ROUND(workflow_restructuring_score::numeric, 3) AS wrs, LENGTH(content_fingerprint) AS fp_len, computed_at FROM dbo.disruption_fingerprints ORDER BY computed_at DESC LIMIT 5"
```

**What to check:**
- `fp_len` is 64 (SHA-256 hex).
- `computed_at` is within the last few seconds.
- Rerunning Step 2 does **not** duplicate rows — `canonical_role_id` is the primary key and `session.merge` replaces metrics in place. Row count stays stable.

### Step 4 — Confirm all 4 disruption patterns are represented

```bash
python scripts/db_check.py query "SELECT cat AS pattern, COUNT(*) AS role_count FROM dbo.disruption_fingerprints, LATERAL jsonb_array_elements_text(disruption_category) AS cat GROUP BY cat ORDER BY role_count DESC"
```

**Expected (multi-period data):** All four labels (`Displacement`, `Augmentation`, `Transformation`, `Emergence`) appear with non-zero counts.

**Expected (single-period data — e.g., all recent JSearch ingestions):** `(no rows)` — the query returns nothing because all `disruption_category` arrays are empty. This is consistent with the §4 Step 2 note: the classifier doesn't fabricate patterns when temporal data doesn't support them. Not a bug.

If one specific pattern is missing despite multi-period data, the classifier thresholds in `analytics/disruption/classifier.py` may not be triggering on your dataset — flag to Pair A.

### Step 5 — Verify `DisruptionRefreshed` event emission

The service publishes `DisruptionRefreshed` only when a bus is registered. The script below attaches an in-process capture bus, runs one refresh, and prints the event payload:

```
python scripts/smoke/disruption_event_check.py
```

**What the script does** (core pattern — same code in `scripts/smoke/disruption_event_check.py`):

```python
class CaptureBus:
    def __init__(self):
        self.events = []
    def publish(self, envelope):
        self.events.append(envelope)

bus = CaptureBus()
svc = DisruptionFingerprintService(event_bus=bus)
with session_scope() as session:
    svc.refresh_disruption_fingerprints(session=session, correlation_id="wk8-event-check")
# bus.events[0] is the DisruptionRefreshed envelope
```

**Expected:** `event_type='DisruptionRefreshed'`, `correlation_id='wk8-event-check'`, `role_count` matches the canonical_roles count, the four category counts (`displacement_count`, `augmentation_count`, `transformation_count`, `emergence_count`) sum to at least `role_count` (a role with multiple labels increments multiple buckets), and `refresh_duration_ms >= 0`.

**If `canonical_roles` is empty:** the script prints a WARNING and exits 1 (no event emitted because no roles were refreshed). Run clustering first per §0 Step 2.

### Step 6 — Content fingerprint stability

Rerun the refresh without changing upstream data and confirm content fingerprints are stable (same inputs → same hash):

```bash
python scripts/db_check.py query "SELECT canonical_role_id, content_fingerprint FROM dbo.disruption_fingerprints ORDER BY canonical_role_id LIMIT 3"
# Rerun Step 2, then:
python scripts/db_check.py query "SELECT canonical_role_id, content_fingerprint FROM dbo.disruption_fingerprints ORDER BY canonical_role_id LIMIT 3"
```

Fingerprints for the same `canonical_role_id` must be identical across runs.

---

## 5. Q&A Pipeline End-to-End (Pairs B + C)

This walks a realistic workforce question through every leg: intent classification → router → SQL guardrails → execute_safe → evidence → synthesis → follow-ups → cost ledger.

### Step 1 — Intent classification in isolation

Run a `trend` question first — expected intent is `trend`:

```
python scripts/smoke/qa_intent_only.py --question "Which AI and machine learning skills are growing fastest in El Paso over the last 90 days?"
```

Then run an `employer` question — expected intent is `employer`:

```
python scripts/smoke/qa_intent_only.py --question "What employers hire the most data analysts?"
```

The two runs should return **different** `intent` values (`trend` vs `employer`). If both return the same intent, the classifier isn't distinguishing question types — flag to Pair B.

The `--correlation-id` flag is optional; use it when you want a human-readable tag for tracing a specific run in `llm_audit_log` or Langfuse (e.g., `--correlation-id wk8-intent-demo`).

**What the script does:**

The intent classifier is the first leg of the Q&A pipeline. It takes a free-text workforce question and returns a structured classification that the downstream router uses to decide which aggregate tables to query. The call flows through `common.llm_adapter.complete` with `role="classification"`, which routes to the Haiku-tier model (`chat-gpt41mini` via `LLM_DEFAULT`) for speed and cost efficiency.

```python
from analytics.query_engine.intent import classify_workforce_question

result = classify_workforce_question(
    question="Which AI and machine learning skills are growing fastest in El Paso?",
    correlation_id="wk8-intent-demo",
)
# Returns a dict with: intent, confidence, needs_clarification, extracted_entities
```

The classifier extracts 4 entity types from the question text:
- **geographic_terms** — place names (El Paso, Las Cruces, Ciudad Juarez, Borderplex subregions)
- **role_names** — job roles mentioned (data engineer, software developer, etc.)
- **skill_names** — skills or technologies mentioned (Python, machine learning, cloud computing)
- **time_references** — temporal phrases (last 90 days, this quarter, Q1 2025)

**The 10 intent categories** — each routes to different aggregate tables downstream:

| Intent | What the user is asking | Routes to |
|--------|------------------------|-----------|
| `trend` | Skill/tool demand changes over time | `skill_demand_weekly`, `tool_demand_weekly`, `skill_velocity` |
| `role_evolution` | How a specific role is changing | `canonical_roles`, `role_snapshot_weekly`, `disruption_fingerprints` |
| `disruption` | Which roles are being displaced, augmented, transformed | `disruption_fingerprints`, temporal period comparisons |
| `emergence` | New roles or skills appearing | Emergence candidate data, `skill_velocity` (high-growth) |
| `curriculum` | What should training programs teach | `skill_demand_weekly` cross-referenced with program data |
| `employer` | What a specific employer or sector needs | `employer_profiles`, `sector_summary_weekly` |
| `workflow` | How work processes are changing | `canonical_roles`, task/responsibility extraction data |
| `geographic` | Regional demand differences | `geo_demand_weekly`, Borderplex subregion data |
| `comparison` | Compare two things (skills, roles, regions, employers) | Cross-table joins based on extracted entities |
| `other` | Doesn't fit the 9 above; or LLM parse failure | Catch-all — cautious response or clarification request |

**`needs_clarification`** triggers when `confidence < 0.55`. This signals the downstream UI to ask the user a disambiguating follow-up instead of routing a low-confidence guess.

**Expected output shape:**

```json
{
  "intent": "trend",
  "confidence": 0.9,
  "needs_clarification": false,
  "extracted_entities": {
    "geographic_terms": ["El Paso"],
    "role_names": [],
    "skill_names": ["AI", "machine learning"],
    "time_references": ["last 90 days"]
  }
}
```

**What to check:**
- `intent` is one of the 10 labels in the table above.
- `confidence` is a float in `[0, 1]`.
- `needs_clarification` is `true` only when `confidence < 0.55`.
- `extracted_entities` always contains the 4 keys, even if lists are empty.
- Cost: ~$0.00025 per classification call (Haiku-tier, ~400–500 tokens total).

### Step 2 — Full guardrailed routing (end-to-end happy path)

Step 1 tested intent classification in isolation. This step fires **every leg** of the Q&A pipeline in sequence: intent classification → SQL generation → SQL guardrails validation → safe execute (30s timeout) → evidence bundle → synthesis (Sonnet-class) → follow-up generation (Haiku-class) → cost ledger rollup. One command exercises all of Pair B's, Pair C's, and Pair D's Week 8 code in a single call.

The question is a `trend` intent designed to hit `skill_demand_weekly` — the most populated aggregate table. `--answer-preview-chars 500` shows the first 500 chars of the synthesized answer so you can assess quality without scrolling.

```
python scripts/smoke/qa_pipeline.py --question "Which 10 skills had the highest posting counts in the most recent week?" --correlation-id wk8-qa-happy --answer-preview-chars 500
```

> **Known issue (#186):** If `_SCHEMA_HINT` in `analytics/query_engine/routing.py` has not been patched with column-level schema, the LLM may generate invalid SQL (e.g., referencing `job_postings.skill_id` which doesn't exist). The pipeline will refuse safely — `refused=True`, synthesis skipped, cost only shows `intent_classification` + `sql_generation`. This is the correct guardrail behavior on a bad query, not a crash. See issue [#186](https://github.com/Building-With-Agents/job-intelligence-engine/issues/186) for the hotfix.

**What the script does:**

```python
from analytics.query_engine.routing import run_guardrailed_analytics_query
from common.data_store.database import session_scope
from common.types.query_request import QueryRequest

req = QueryRequest(query=question)
with session_scope() as s:
    resp = run_guardrailed_analytics_query(req, session=s, correlation_id=correlation_id)
# Prints: answer, citations, confidence, refused, follow_ups, cost_breakdown_usd
```

**Expected output (happy path):**

- `answer_text`: 1–3 sentences grounded in real aggregate data (real skill names, real counts). No invented statistics.
- `citations`: between 3 and 10 `EvidenceCitation` items, each with `citation_id`, `summary`, `source_table` (e.g. `skill_demand_weekly`), `supporting_count`, `time_period`.
- `periods_described`: human-readable ISO range or description (never empty — the bundle requires one).
- `confidence`: typically `0.55–0.90` on real data.
- `confidence_flagged_low`: `true` only when confidence < `CONFIDENCE_TRANSPARENCY_THRESHOLD`.
- `volume_flagged_low`: `true` only when the supporting posting count is below `VOLUME_WARNING_POSTING_THRESHOLD`.
- `refused`: `false` for happy paths. `true` only when evidence is insufficient or grounding retry fails.
- `follow_up_questions`: 2–3 contextual suggestions (Haiku-tier).
- `cost_breakdown_usd`: keys include `intent_classification`, `sql_generation`, `synthesis`, `follow_up` (values in USD). Sum equals `total_cost_usd` (to 6 decimal places).

**Expected output (before [#186](https://github.com/Building-With-Agents/job-intelligence-engine/issues/186) hotfix lands):**

Until the `_SCHEMA_HINT` is expanded with column-level schema, the LLM will hallucinate column names that don't exist on the actual tables (e.g., `posted_date` instead of `publish_date`, `skill_id` on `job_postings` which has no such column). The pipeline refuses safely:

- `refused=True`
- `refusal_message` includes `sql_execution_failed:ProgrammingError` with the specific Postgres error
- `cost_breakdown_usd` only contains `intent_classification` + `sql_generation` — synthesis is skipped (no LLM fabrication)
- `citations=0`, `follow_ups=0`

This is the correct guardrail behavior — see [#186](https://github.com/Building-With-Agents/job-intelligence-engine/issues/186) for the root cause and proposed fix. Once #186 lands, this step should produce the happy-path output described above.

### Step 3 — Inspect SQL guardrails in isolation

Step 2 ran the full pipeline — when the LLM generates SQL, it passes through a **guardrail validator** before touching the database. This step tests that validator directly with known-good SQL to verify it allows legitimate queries and correctly normalizes LIMIT clauses.

The JIE has **two guardrail functions** serving different table scopes:

- **`validate_ask_the_data_sql()`** — validates SQL against the **operational** table allowlist (`ASK_THE_DATA_ALLOWED_TABLES`: `job_postings`, `companies`, `skills`, etc.). Used by the Ask-the-Data Streamlit page path.
- **`validate_sql()`** — validates SQL against the **aggregate** table allowlist (`ALLOWED_TABLES`: `skill_demand_weekly`, `canonical_roles`, etc.). Used by the FastAPI trigger endpoints and the routing pipeline. Returns a `ValidationResult` object with `.ok`, `.reason`, and `.sql_for_execution` (with LIMIT normalized).

Both enforce: SELECT-only, single-statement, schema-scoped tables, and a row cap (`MAX_ROWS=100`). If a query requests `LIMIT 500`, the guardrail rewrites it to `LIMIT 100` rather than rejecting — safe capping, not hard failure.

```
python scripts/smoke/sql_guardrails_check.py
```

**What the script does:**

```python
from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql, validate_sql

# Test 1: Ask-the-Data path — job_postings is in the operational allowlist → ok=True
validate_ask_the_data_sql("SELECT job_title, location FROM dbo.job_postings LIMIT 20")

# Test 2: Ask-the-Data path — skill_demand_weekly is NOT in the operational allowlist → ok=False
validate_ask_the_data_sql("SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 50")

# Test 3: Aggregate path — LIMIT 500 gets capped to 100 → ok=True, sql rewritten
validate_sql("SELECT skill_label, posting_count FROM dbo.skill_demand_weekly LIMIT 500")

# Test 4: Aggregate path — LIMIT 10 within cap → ok=True, sql unchanged
validate_sql("SELECT skill_label, posting_count FROM dbo.skill_demand_weekly ORDER BY posting_count DESC LIMIT 10")
```

The script prints the input SQL and the result for each test, with PASS/FAIL per case and a summary.

**Expected:** 4/4 passed:
- Test 1: `ok=True` — `job_postings` is in the Ask-the-Data allowlist
- Test 2: `ok=False`, `reason=disallowed_table:skill_demand_weekly` — aggregate tables aren't in the operational allowlist (intentional separation)
- Test 3: `ok=True`, `sql_for_execution` has `LIMIT 100` (capped from 500)
- Test 4: `ok=True`, `sql_for_execution` has `LIMIT 10` (within cap, unchanged)

§7 covers the full adversarial test suite (DROP, UNION, injection, etc.). This step just confirms the happy path and the allowlist boundary.

**Interactive testing — validate any SQL string against either allowlist:**

To test your own SQL against the **operational** (Ask-the-Data) allowlist:

```
python scripts/smoke/validate_atd_sql.py "SELECT job_title FROM dbo.job_postings LIMIT 10"
python scripts/smoke/validate_atd_sql.py "DROP TABLE dbo.job_postings"
python scripts/smoke/validate_atd_sql.py "SELECT * FROM dbo.skill_demand_weekly"
```

To test against the **aggregate** (trigger/routing) allowlist:

```
python scripts/smoke/validate_agg_sql.py "SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 10"
python scripts/smoke/validate_agg_sql.py "SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 5000"
python scripts/smoke/validate_agg_sql.py "SELECT * FROM dbo.job_postings"
```

These accept any SQL string as an argument and print the guardrail's verdict (`PASS` or `REJECTED` with reason). Useful for exploring the boundary between the two allowlists — e.g., `skill_demand_weekly` passes the aggregate guardrail but is rejected by the Ask-the-Data guardrail, while `job_postings` is the reverse.

### Step 4 — Verify `execute_safe` enforces the 30s timeout

After the guardrail validates the SQL, `execute_validated_query()` runs it against Postgres with a **30-second timeout** at the session level (`SET LOCAL statement_timeout = 30000`). This is the last safety layer before results reach the evidence builder — it prevents runaway queries from blocking the pipeline or the database.

Every Q&A path, every trigger endpoint, and every Ask-the-Data call flows through this function. If a query exceeds 30 seconds, it's killed by Postgres and surfaces as `RuntimeError("query_timeout")` — the pipeline then refuses with a clean error, no partial results.

```
python scripts/smoke/execute_safe_check.py
```

**What the script does:**

```python
from analytics.query_engine.execute_safe import execute_validated_query
from common.data_store.database import session_scope

with session_scope() as s:
    rows, n = execute_validated_query(s, 'SELECT 1 AS ping')
    # Returns (list[dict], int) — the result rows and row count
```

**Expected:** `rows=[{'ping': 1}]`, `count=1`. A trivial `SELECT 1` confirms the function works end-to-end: session-level timeout is set, the query runs, rows are returned as a list of dicts.

Timeout behavior (not tested here, but important to know): if you ran a query like `SELECT pg_sleep(60)`, it would be killed after 30 seconds and raise `RuntimeError("query_timeout")`. The raw driver message is logged via structlog but not surfaced to the caller.

### Step 5 — Check grounding retry path (hallucination guard)

This step tests the **grounding safety** of the synthesis layer. When the LLM generates an answer, the grounding check verifies that any numbers in the answer actually appear in the evidence citations. If the first answer mentions statistics not present in the citations (hallucination), synthesis retries with a stronger grounding prompt. If the retry also fails, the response is flagged with `confidence_flagged_low=true` or refused entirely.

The test question is intentionally narrow — asking about salary data for a specific role in a specific location and time period. On most dev databases this will produce thin or empty evidence, triggering one of:
- `volume_flagged_low=true` — answer is based on fewer than 30 postings (transparent caveat, not a refusal)
- `confidence_flagged_low=true` — LLM confidence dropped below 0.6 (answer is still returned, but flagged)
- `refused=True` — evidence is absent entirely, synthesis refuses rather than fabricating

Run a narrow salary question — expected to produce thin or empty evidence:

```
python scripts/smoke/qa_grounding_check.py --question "What is the median salary for data engineers in El Paso this quarter?"
```

Then try a question with even thinner evidence to compare behavior — this should trigger a stronger refusal or lower confidence:

```
python scripts/smoke/qa_grounding_check.py --question "What is the average salary for cloud architects in Las Cruces?"
```

> **Known issue (#186):** Both questions may hit the `_SCHEMA_HINT` gap and refuse at the SQL execution stage rather than the grounding stage. This is the same #186 behavior as Step 2. Once #186 lands, this step will exercise the grounding path as designed.

**What the script does:**

```python
from analytics.query_engine.routing import run_guardrailed_analytics_query
from common.data_store.database import session_scope
from common.types.query_request import QueryRequest

req = QueryRequest(query="What is the median salary for data engineers in El Paso this quarter?")
with session_scope() as s:
    resp = run_guardrailed_analytics_query(req, session=s, correlation_id="wk8-grounding")
# Prints: answer_len, refused, refusal_message, confidence, flagged_low, volume_flagged_low
```

**What to check:**
- If the evidence is thin: `confidence_flagged_low` or `volume_flagged_low` is `true` and `answer_text` is short / cautious (no hallucinated numbers).
- If evidence is absent entirely: `refused=True` with a non-empty `refusal_message`.
- The key property: the system **never invents a salary number** that isn't in the evidence. If it can't answer honestly, it says so.

---

## 6. FastAPI API Smoke Test (Pair D)

### Step 1 — Start the API

In a **separate terminal** (keep it running throughout §6):

```
python scripts/run_analytics_api.py
```

**Expected console output:**

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

### Step 2 — Verify OpenAPI `/docs` loads

FastAPI auto-generates an interactive **Swagger UI** at `/docs` from the route decorators and Pydantic schemas in `analytics/api/routes.py` and `analytics/api/schemas.py`. Opening it in a browser confirms three things at once:

1. **The API server booted cleanly** — if any import fails (missing dep, broken module), the app won't start and `/docs` returns 404.
2. **All 5 routes are registered** — if a route decorator is malformed or the router wasn't mounted in `analytics/api/app.py`, it won't appear in the UI.
3. **Request/response schemas are valid** — Swagger renders the Pydantic models as interactive forms. If a schema has a type error, Swagger shows a parse warning. You can also use the "Try it out" button to send test requests directly from the browser without curl.

Open http://127.0.0.1:8000/docs in a browser.

**Expected:** Swagger UI lists all five endpoints under the `analytics` tag:
- `POST /analytics/query`
- `POST /analytics/triggers/cohort_gap_analysis`
- `POST /analytics/triggers/role_benchmark`
- `POST /analytics/triggers/emerging_skills_scan`
- `POST /analytics/triggers/custom_employer_comparison`

The raw JSON schema is at http://127.0.0.1:8000/openapi.json. This is the machine-readable version — useful for generating API clients or validating the contract programmatically.

### Steps 3–5 — Full API smoke (all endpoints + cache + adversarial)

One script exercises the entire API surface — no curl required, works on all shells:

```
python scripts/smoke/api_smoke.py
```

**What the script does:**

The script sends HTTP requests to the running API using Python's `urllib` (no external deps). It covers 6 checks in order:

1. **POST /analytics/query** — sends a skills question through the full Q&A pipeline. Will hit [#186](https://github.com/Building-With-Agents/job-intelligence-engine/issues/186) (expected `refused=True`). Use `--skip-query` to skip this step if you only want triggers.
2. **POST /analytics/triggers/cohort_gap_analysis** — parameterized SQL, no LLM. Returns market skill demand for a cohort key.
3. **POST /analytics/triggers/role_benchmark** — benchmarks a canonical role. Pass `--role-id <uuid>` with a real `canonical_role_id` from your DB for meaningful results.
4. **POST /analytics/triggers/emerging_skills_scan** — scans for trending skills.
5. **POST /analytics/triggers/custom_employer_comparison** — benchmarks an employer against the market.
6. **Cache verification** — re-runs `cohort_gap_analysis` with the same params. Second call should return `cached=true` with the same `computed_at` timestamp.
7. **Adversarial injection** — sends `"bobby; DROP TABLE students--"` as a `canonical_role_id` to `role_benchmark`. Should return HTTP 400 (`invalid_role_id`).

**Optional flags:**

```
python scripts/smoke/api_smoke.py --skip-query                  # skip the Q&A endpoint (blocked by #186)
python scripts/smoke/api_smoke.py --question "Custom question"  # different Q&A question
python scripts/smoke/api_smoke.py --role-id <uuid>              # use a real canonical_role_id
python scripts/smoke/api_smoke.py --base-url http://host:port   # non-default API host
```

**Expected output (summary):**

```
SUMMARY
  PASS  POST /analytics/query               (or FAIL if #186 not yet fixed — expected)
  PASS  POST /analytics/triggers/cohort_gap_analysis
  PASS  POST /analytics/triggers/role_benchmark
  PASS  POST /analytics/triggers/emerging_skills_scan
  PASS  POST /analytics/triggers/custom_employer_comparison
  PASS  Cache hit on 2nd call
  PASS  Adversarial rejection (expect 400)
```

All 4 trigger endpoints should PASS. The Q&A endpoint may FAIL until [#186](https://github.com/Building-With-Agents/job-intelligence-engine/issues/186) lands. Cache and adversarial should always PASS.

After the smoke, verify the cache table directly:

```
python scripts/db_check.py query "SELECT trigger_type, cohort_key, computed_at, expires_at FROM dbo.cohort_gap_cache ORDER BY computed_at DESC LIMIT 5"
```

`expires_at` should be ~24 hours after `computed_at`.

### Individual curl commands (reference)

The script above covers all of these. Use these if you want to hit a specific endpoint manually or see the raw HTTP contract. **PowerShell note:** use `curl.exe` (not the PowerShell `curl` alias which is `Invoke-WebRequest`). Assign the JSON body to a variable to avoid quoting issues.

**POST /analytics/query:**

bash / git-bash:
```bash
curl -sS -X POST http://127.0.0.1:8000/analytics/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Which 5 skills have the highest posting counts?","correlation_id":"api-smoke-1"}' | python -m json.tool
```

PowerShell:
```powershell
$body = '{"question":"Which 5 skills have the highest posting counts?","correlation_id":"api-smoke-1"}'
curl.exe -sS -X POST http://127.0.0.1:8000/analytics/query -H "Content-Type: application/json" -d $body | python -m json.tool
```

**Trigger endpoints:**

bash / git-bash:
```bash
curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/cohort_gap_analysis \
  -H "Content-Type: application/json" \
  -d '{"cohort_key":"demo-cohort","week_start":null}' | python -m json.tool

curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark \
  -H "Content-Type: application/json" \
  -d '{"canonical_role_id":"role_1","week_start":null}' | python -m json.tool

curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/emerging_skills_scan \
  -H "Content-Type: application/json" \
  -d '{"scan_key":"smoke-test"}' | python -m json.tool

curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/custom_employer_comparison \
  -H "Content-Type: application/json" \
  -d '{"company_id":"company_42","week_start":null}' | python -m json.tool
```

PowerShell:
```powershell
$body = '{"cohort_key":"demo-cohort","week_start":null}'
curl.exe -sS -X POST http://127.0.0.1:8000/analytics/triggers/cohort_gap_analysis -H "Content-Type: application/json" -d $body | python -m json.tool

$body = '{"canonical_role_id":"role_1","week_start":null}'
curl.exe -sS -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark -H "Content-Type: application/json" -d $body | python -m json.tool

$body = '{"scan_key":"smoke-test"}'
curl.exe -sS -X POST http://127.0.0.1:8000/analytics/triggers/emerging_skills_scan -H "Content-Type: application/json" -d $body | python -m json.tool

$body = '{"company_id":"company_42","week_start":null}'
curl.exe -sS -X POST http://127.0.0.1:8000/analytics/triggers/custom_employer_comparison -H "Content-Type: application/json" -d $body | python -m json.tool
```

**Adversarial SQL injection via trigger:**

bash / git-bash:
```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark \
  -H "Content-Type: application/json" \
  -d '{"canonical_role_id":"bobby; DROP TABLE students--"}'
```

PowerShell:
```powershell
$body = '{"canonical_role_id":"bobby; DROP TABLE students--"}'
curl.exe -sS -o NUL -w "HTTP %{http_code}`n" -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark -H "Content-Type: application/json" -d $body
```

**Expected:** `HTTP 400`. Response body has `{"detail": "invalid_role_id"}`.

---

## 7. SQL Guardrails — Adversarial Verification

> **How this differs from §5 Step 3:** Step 3 tested the guardrails with **valid SQL** (happy path — does a legitimate query pass? does LIMIT get capped correctly?). This section tests with **malicious SQL** (adversarial path — does DROP TABLE get blocked? does UNION into `pg_shadow` get caught? does comment injection bypass the check?). Step 3 answers "does it let good queries through?" — this section answers "does it stop bad queries?"

The Q&A pipeline accepts natural-language questions, not raw SQL — users never type SQL directly. But the pipeline generates SQL internally (via the LLM in `routing.py`), and that generated SQL passes through the guardrail **before** it reaches the database. The guardrail is the **hard backstop**: even if the LLM is tricked or hallucinates dangerous SQL, the guardrail blocks it.

This section tests the guardrail **directly with raw SQL strings**, bypassing the LLM entirely. This is intentional — LLM output is non-deterministic, so testing the guardrail through the LLM would produce flaky results. By feeding known-bad SQL directly into `validate_ask_the_data_sql()` and `validate_sql()`, we verify every rejection path deterministically.

**Why two guardrails?** The JIE has two SQL execution contexts with different security boundaries:

- **`validate_ask_the_data_sql()`** — the Ask-the-Data Streamlit page where users ask natural-language questions. Allowlist is **operational** tables (`job_postings`, `companies`, `skills`, etc.). More restrictive because the LLM generates the SQL from user input.
- **`validate_sql()`** — the trigger endpoints and internal routing pipeline. Allowlist is **aggregate** tables (`skill_demand_weekly`, `canonical_roles`, etc.). Still guarded, but the SQL patterns are more constrained (parameterized templates, not free-form LLM output).

Both enforce: SELECT-only (no DDL/DML), single-statement (no `;` chaining), schema-scoped (`dbo.*` only), and a row cap (`LIMIT 100` max). The guardrails use **sqlglot AST parsing** (not regex) — this means attacks like comment injection (`-- ; DROP TABLE`) or whitespace obfuscation are caught because sqlglot sees the parsed tree, not the raw text.

### Step 1 — Run the full adversarial test suite

```
python scripts/smoke/sql_guardrails_adversarial.py
```

This runs 15 test cases: 9 against the Ask-the-Data (operational) guardrail and 6 against the aggregate guardrail. Each case is a known-bad SQL string designed to exploit a specific attack vector:

**Ask-the-Data adversarial cases (9 — all should REJECT):**

| Case | Attack vector | SQL | Expected reason |
|------|--------------|-----|-----------------|
| DROP TABLE | DDL injection | `DROP TABLE dbo.job_postings` | `forbidden_keyword` |
| INSERT | DML injection | `INSERT INTO dbo.job_postings (id) VALUES (1)` | `forbidden_keyword` |
| UNION over disallowed | Data exfiltration via UNION | `SELECT 1 UNION SELECT * FROM pg_shadow` | `disallowed_table:pg_shadow` |
| multi-statement | Statement chaining | `SELECT 1; SELECT 2` | `multiple_statements` |
| comment bypass | Hide malicious SQL after `--` | `SELECT * FROM dbo.job_postings -- ; DROP TABLE x` | `multiple_statements` |
| subquery forbidden | Subquery into system table | `SELECT * FROM dbo.job_postings WHERE id IN (SELECT id FROM pg_authid)` | `disallowed_table:pg_authid` |
| disallowed schema | Access `pg_catalog` | `SELECT * FROM pg_catalog.pg_tables` | `disallowed_schema:pg_catalog` |
| disallowed table | Access `llm_audit_log` (protected) | `SELECT * FROM dbo.llm_audit_log` | `disallowed_table:llm_audit_log` |
| not SELECT | TRUNCATE disguised | `TRUNCATE TABLE dbo.job_postings` | `forbidden_keyword` |

**Aggregate adversarial cases (6 — 5 should REJECT, 1 should PASS with cap):**

| Case | SQL | Expected |
|------|-----|----------|
| DROP in aggregate | `DROP TABLE dbo.skill_demand_weekly` | `ok=False` |
| INSERT in aggregate | `INSERT INTO dbo.skill_demand_weekly (id) VALUES (1)` | `ok=False` |
| UPDATE forbidden | `UPDATE dbo.skill_demand_weekly SET posting_count=0` | `ok=False` |
| multi-statement | `SELECT 1; SELECT 2` | `ok=False` |
| non-allowlisted table | `SELECT * FROM dbo.job_postings` | `ok=False` (job_postings is operational, not aggregate) |
| LIMIT over cap | `SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 5000` | `ok=True`, LIMIT rewritten to 100 |

**Expected:** `15/15 passed`.

**Interactive testing — validate your own SQL:**

Use the interactive scripts from §5 Step 3 to test any SQL string against either guardrail:

```
python scripts/smoke/validate_atd_sql.py "SELECT * FROM pg_catalog.pg_tables"
python scripts/smoke/validate_atd_sql.py "SELECT job_title FROM dbo.job_postings LIMIT 10"
python scripts/smoke/validate_agg_sql.py "SELECT * FROM dbo.job_postings"
python scripts/smoke/validate_agg_sql.py "SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 10"
```

These accept any SQL string as an argument and print `PASS` or `REJECTED` with the specific reason. Try crafting your own attack vectors — if one gets through, that's a security finding to flag.

### Step 2 — Confirm guardrail audit rows exist from API-path calls

Guardrail validation results are logged to `dbo.llm_audit_log` **only when called through the FastAPI route handlers** (not when calling `validate_sql` / `validate_ask_the_data_sql` directly from smoke scripts). This is the same audit-coverage gap as [#187](https://github.com/Building-With-Agents/job-intelligence-engine/issues/187).

The §6 API smoke test (`api_smoke.py`) and any earlier `POST /analytics/query` calls should have produced audit rows:

```
python scripts/db_check.py query "SELECT agent_name, model, provider, success, error_reason, created_at FROM dbo.llm_audit_log WHERE agent_name='analytics_query_api' ORDER BY created_at DESC LIMIT 10"
```

**Expected:**
- `agent_name='analytics_query_api'`, `model='sql-guardrail'`, `provider='internal'`
- `success=true` rows from trigger endpoints (valid parameterized SQL)
- `success=false` rows from Q&A calls where LLM-generated SQL was rejected (e.g., `error_reason='disallowed_table:skill_demand_weekly'`)
- The §7 Step 1 adversarial test cases will **NOT** appear here — they called the guardrail directly, not through the API. This is a known limitation tracked in [#187](https://github.com/Building-With-Agents/job-intelligence-engine/issues/187).

### Step 3 — End-to-end adversarial via the API

Step 1 tested the guardrail functions directly. This step fires a malicious payload through the **full HTTP path** to confirm the rejection travels cleanly from guardrail → route handler → HTTP 400 response (no 500, no stack trace, no crash).

If you already ran `api_smoke.py` in §6, this was covered — the script includes an adversarial injection test. You can re-run just that check, or try your own injection string:

```
python scripts/smoke/api_smoke.py --skip-query
python scripts/smoke/api_smoke.py --skip-query --adversarial-role-id "'; DROP TABLE dbo.job_postings; --"
python scripts/smoke/api_smoke.py --skip-query --adversarial-role-id "UNION SELECT * FROM pg_shadow"
```

The adversarial test is the last item in the summary — look for `Adversarial rejection (expect 400): PASS`.

**What the test does:** sends the `--adversarial-role-id` string (default: `bobby; DROP TABLE students--`) as `canonical_role_id` to `POST /analytics/triggers/role_benchmark`. The route handler's `_require_safe_token()` guard rejects the input before it reaches SQL generation. The API returns HTTP 400 with `{"detail": "invalid_role_id"}` — a structured error, not a crash.

**Reminder — interactive guardrail testing from §5 Step 3:**

For experimenting with raw SQL against the guardrails directly (not through the API), use:

```
python scripts/smoke/validate_atd_sql.py "YOUR SQL HERE"    # operational allowlist
python scripts/smoke/validate_agg_sql.py "YOUR SQL HERE"    # aggregate allowlist
```

These test the guardrail logic itself; the `api_smoke.py --adversarial-role-id` flag tests the HTTP-layer input guard (`_require_safe_token`) which fires before SQL is even generated.

**Individual curl (reference):**

bash / git-bash:
```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark \
  -H "Content-Type: application/json" \
  -d '{"canonical_role_id":"bobby; DROP TABLE students--"}'
```

PowerShell:
```powershell
$body = '{"canonical_role_id":"bobby; DROP TABLE students--"}'
curl.exe -sS -o NUL -w "HTTP %{http_code}`n" -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark -H "Content-Type: application/json" -d $body
```

**Expected:** `HTTP 400`. Response body: `{"detail": "invalid_role_id"}`.

---

## 8. Audit Log Verification

The audit log provides a complete trail of every Q&A interaction for observability, cost accounting, and debugging. Juan | Enrique (Pair D) built the writer (`analytics/query_engine/audit_log.py`), the ORM model (`common/data_store/models.py::OrchestrationAuditLog`), and the route-handler integration. Every request that hits the FastAPI layer writes an audit row with the question hash, generated SQL hash, confidence score, and success status.

Two known limitations:
- **#187 — Audit only at the API layer.** Direct function calls (e.g., `scripts/smoke/qa_pipeline.py`, `sql_guardrails_adversarial.py`) bypass the route handlers and do not write audit rows. Only HTTP requests to the FastAPI endpoints are audited.
- **#188 — `success` reflects HTTP status, not answer quality.** A row with `success=true, confidence=0.0` may be a quality failure (refusal, low evidence). The `success` field means "the route handler returned 200," not "the answer was good."

### Step 1 — Confirm table and columns

```bash
python scripts/db_check.py query "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema='dbo' AND table_name='orchestration_audit_log' ORDER BY ordinal_position"
```

**Expected columns:** `id`, `created_at`, `correlation_id`, `endpoint`, `question_hash`, `sql_hash`, `confidence`, `success`, `error_code`, `payload`.

### Step 2 — Run the audit log smoke test

```
python scripts/smoke/audit_log_check.py
```

**What the script does** (core body — same code in `scripts/smoke/audit_log_check.py`):

```python
import json, urllib.request
from common.data_store.database import session_scope
from common.data_store.models import OrchestrationAuditLog

# 1. Send 3 Q&A requests with distinct correlation IDs
for cid in ["audit-check-1", "audit-check-2", "audit-check-3"]:
    data = json.dumps({"question": "Top skills by posting count", "correlation_id": cid}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req)

# 2. Send 1 adversarial request
data = json.dumps({"canonical_role_id": "bad;role--injection", "correlation_id": "audit-check-adversarial"}).encode()
req = urllib.request.Request(trigger_url, data=data, headers={"Content-Type": "application/json"}, method="POST")

# 3. Query audit log for those correlation IDs and verify:
#    - rows exist for each happy-path correlation_id
#    - endpoint field is populated
#    - question_hash length = 64 (SHA-256 hex)
#    - success field is populated
#    - adversarial row present (may be missing per #187)
with session_scope() as session:
    rows = session.query(OrchestrationAuditLog).filter(
        OrchestrationAuditLog.correlation_id.in_(all_cids)
    ).all()
```

**Expected output:**

- 3 PASS results for happy-path correlation IDs (`audit-check-1`, `audit-check-2`, `audit-check-3`).
- `endpoint` populated, `question_hash` length 64, `success` populated for each.
- Adversarial audit row may show FAIL — this is expected per #187 (audit writes happen only at the route-handler layer; rejected triggers may not audit). Not a blocker.

**Optional flags:**
- `--base-url http://127.0.0.1:8000` — override API base URL
- `--question "Which skills are trending?"` — change the test question

### Step 3 — Verify audit rows manually (reference)

For manual inspection of audit rows after the smoke test or after demo runs:

```bash
python scripts/db_check.py query "SELECT created_at, correlation_id, endpoint, LENGTH(question_hash) AS qh_len, LENGTH(sql_hash) AS sh_len, confidence, success, error_code FROM dbo.orchestration_audit_log ORDER BY created_at DESC LIMIT 5"
```

**What to check:**
- `correlation_id` matches the IDs you passed.
- `endpoint` is `/analytics/query` for Q&A calls, `/analytics/triggers/<name>` for trigger calls.
- `qh_len = 64` (SHA-256 hex of the question).
- `sh_len = 64` (SHA-256 hex of the generated SQL).
- `confidence` is a float; `success = true` on happy paths.
- `error_code` is `NULL` on success.

### Step 4 — Failure-path audit (reference)

Force a failure manually and check if a failure-path row was captured:

**bash:**
```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark \
  -H "Content-Type: application/json" \
  -d '{"canonical_role_id":"bad;role","correlation_id":"audit-fail-1"}'
```

**PowerShell:**
```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/analytics/triggers/role_benchmark -Method POST -ContentType "application/json" -Body '{"canonical_role_id":"bad;role","correlation_id":"audit-fail-1"}' -ErrorAction SilentlyContinue
```

Then check for the row:

```bash
python scripts/db_check.py query "SELECT correlation_id, endpoint, success, error_code FROM dbo.orchestration_audit_log WHERE correlation_id='audit-fail-1'"
```

The route handler translates `ValueError` to `HTTPException 400`. Currently audit rows are written from the happy-path handler; failure-path auditing for triggers is tracked in the Pair D follow-up list (#187). If no row appears for this correlation_id, that is expected behavior — flag it in the demo debrief, not a blocker.

---

## 9. Streamlit Dashboard — New Pages

The dashboard is the user-facing layer of the JIE analytics stack. It calls the FastAPI API over HTTP — it never imports agent code directly. This architecture boundary is enforced by `dashboard/analytics_query_client.py`, which wraps all HTTP calls to the API. Bryan | Emilio (Pair C) own the Ask-the-Data page; Juan | Enrique (Pair D) own the 3 trigger visualization pages (Skills Gap Map, Emergence Alerts, Regional Heatmap).

### Prerequisite — Week 7 aggregate tables MUST be populated

Most Week 8 Streamlit pages read from Week 7 aggregate tables. If these tables are empty, pages will render but show "no data" states — they will not crash, but they will not display any charts or meaningful content.

**Minimum table population required per page:**

| Page | Required tables | Populated by |
|------|----------------|-------------|
| Weekly Insights | `skill_demand_weekly` | Week 7 analytics aggregates (`verify_aggregates.py`) |
| Skills Gap Map | `cohort_gap_cache` (with non-empty `gap_data`) | API trigger `POST /analytics/triggers/cohort_gap_analysis` — but returns empty `market_skill_demand` if `skill_demand_weekly` is empty |
| Emergence Alerts | `disruption_fingerprints` + `canonical_roles` | §4 disruption refresh + §0 Step 2 clustering |
| Regional Heatmap | `geo_demand_weekly` | Week 7 analytics geo-demand aggregation step |
| Ask the Data | `skill_demand_weekly`, operational tables | Q&A pipeline needs aggregate evidence to answer questions |

**If pages show "no data" on refresh, check table row counts first:**

```
python scripts/week8_verify_counts.py
```

If the Week 7 aggregate tables show `(empty)` or `0`, populate them:

```
python scripts/refresh_aggregates.py
```

**Optional flags:**

```
python scripts/refresh_aggregates.py --week 2026-04-14      # target a specific Monday
python scripts/refresh_aggregates.py --skip-pipeline         # steps 2,3,8,9 only (skip sector/geo)
```

**What the script does:**

1. **Step A — `process_aggregates`** calls four aggregator functions that scan `dbo.extracted_intelligence` and roll up counts per ISO week:
   - `refresh_skill_demand_weekly` (step 2) — counts how many postings mention each skill per week → `dbo.skill_demand_weekly`
   - `refresh_tool_demand_weekly` (step 3) — same for tools → `dbo.tool_demand_weekly`
   - `refresh_skill_velocity` (step 8) — week-over-week change rates for skills → `dbo.skill_velocity`
   - `refresh_skill_co_occurrence` (step 9) — which skills appear together → `dbo.skill_co_occurrence`
   
   The target week defaults to the most recent Monday before the current date. All four functions scan the same underlying data, so if `extracted_intelligence` has rows, these tables will populate.

2. **Step B — `run_pipeline`** runs the full 13-step analytics batch, which adds two more tables not covered by Step A:
   - `compute_sector_summary_weekly` (step 6) — posting/employer counts grouped by NAICS sector → `dbo.sector_summary_weekly`
   - `compute_geo_demand_weekly` (step 7) — posting counts grouped by Borderplex subregion → `dbo.geo_demand_weekly`
   
   **Why these can still be empty:** `run_pipeline` computes its own `week_start` from the current date independently of Step A. If the postings in `job_postings` all have dates in a different week than the one `run_pipeline` targets, the SQL `WHERE date_posted >= week_start AND date_posted < week_start + 7 days` returns zero rows. This is a known week-alignment gap — pass `--week` with the Monday that matches your posting dates (check with `SELECT MIN(date_posted), MAX(date_posted) FROM dbo.job_postings`).

3. **Step C — Verification** queries `COUNT(*)` on each aggregate table and prints OK / (empty).

**Expected output:**

```
--- Step A: process_aggregates (steps 2, 3, 8, 9) ---
  skill demand weekly              <N>
  tool demand weekly               <N>
  skill velocity                   <N>
  skill co occurrence              <N>

--- Step B: run_pipeline (steps 6, 7 — sector + geo demand) ---
  pipeline completed

--- Verification ---
  skill_demand_weekly               <N>  OK
  tool_demand_weekly                <N>  OK
  sector_summary_weekly             <N>  OK    (may be 0 — see week alignment note above)
  geo_demand_weekly                 <N>  OK    (may be 0 — see week alignment note above)
  skill_velocity                    <N>  OK
  skill_co_occurrence               <N>  OK
  posting_freshness                 <N>  OK
```

If Step B prints `pipeline skipped (minimum data guard)`, there are not enough enriched postings in `dbo.job_postings` — check §3 upstream data.

**Why some aggregate tables may still be empty after running this script:**

The sector and geo aggregators (steps 6, 7) filter postings by date to bucket them into ISO weeks. If `job_postings.publish_date` is mostly NULL — which it is on current JSearch-ingested data — those aggregators find zero postings in any week and return empty results. This is tracked in [#172](https://github.com/Building-With-Agents/job-intelligence-engine/issues/172) (promote `date_posted` from `normalized_jobs` to `job_postings`). Once #172 lands and the backfill runs, re-running this script will populate all aggregate tables with multi-week data. See [#189](https://github.com/Building-With-Agents/job-intelligence-engine/issues/189) for the follow-up steps.

After aggregates are populated, run `api_smoke.py` (Step 2 below) to pre-warm the cache tables with real data from the populated aggregates.

### Step 1 — Validate page imports (smoke test)

Before launching Streamlit, confirm all 4 new page modules import cleanly:

```
python scripts/smoke/streamlit_check.py
```

**What the script does** (core body — same code in `scripts/smoke/streamlit_check.py`):

```python
import importlib

pages = [
    "dashboard.pages_ask_the_data",
    "dashboard.pages_skills_gap_map",
    "dashboard.pages_emergence_alerts",
    "dashboard.pages_regional_heatmap",
]
for module_path in pages:
    importlib.import_module(module_path)  # raises ImportError on missing deps
```

**Expected output:** All 4 pages show `OK`. If any show `FAIL`, the error message will name the missing dependency — typically `streamlit`, `plotly`, or a module under `analytics/` that needs a post-merge `pip install -r requirements.txt`.

### Step 2 — Pre-populate caches for the dashboard

The trigger visualization pages read from cache tables that are populated by the API triggers. Before launching Streamlit, pre-warm the caches using the API smoke test (requires the API to be running — see §6):

```
python scripts/smoke/api_smoke.py
```

This hits all 4 trigger endpoints and populates `dbo.cohort_gap_cache`, `dbo.disruption_fingerprints` (via §4), and the other cache tables. See §6 for full details on what the smoke test covers.

> **Important:** If the upstream aggregate tables (`skill_demand_weekly`, `geo_demand_weekly`, etc.) are empty, the triggers will succeed but write empty or stub data to the cache tables. The pages will then render without errors but show no charts. Always verify aggregate table population first (see Prerequisite above).

### Step 3 — Launch Streamlit

```bash
streamlit run dashboard/app.py
```

Open http://localhost:8501. The sidebar lists all four new Week 8 pages:

- Ask the Data
- Skills Gap Map
- Emergence Alerts
- Regional Heatmap

### Ask the Data (Pair C)

Consumes `/analytics/query` (or the direct Python path when the API is not running — check `dashboard/pages_ask_the_data.py` for the live config).

**Note:** Ask-the-Data is currently blocked by #186 (`_SCHEMA_HINT` missing columns). The LLM may hallucinate column names (`skill_id`, `posted_date`, `company_id`) that do not exist in the aggregate tables, triggering the refusal path. The refusal fires correctly — synthesis is skipped, no fabrication — but happy-path answers may not appear until #186 is resolved.

**Verification checklist:**

| Element | Expected |
|---------|----------|
| Chat input | Accepts natural-language workforce questions |
| Answer panel | Shows synthesis output with inline citation markers |
| Confidence banner | Shown when `confidence_flagged_low=true` or `volume_flagged_low=true` |
| Refusal banner | Shown in red when `refused=true`; `refusal_message` visible |
| Citations list | Each citation shows `source_table`, `supporting_count`, `time_period` |
| Follow-up chips | 2–3 clickable chips that re-fire the chat with the selected suggestion |
| Cost readout (optional) | Shows `total_cost_usd` for the turn — Pair C implementation detail |

Ask a few probes to exercise each code path:

1. **Happy path:** "Top 10 skills by posting count in the most recent week" → grounded answer + 3+ citations + follow-ups.
2. **Sparse:** "Median salary for data engineers in El Paso this quarter" → `volume_flagged_low=true` or refusal.
3. **Off-topic:** "What's the weather in El Paso?" → intent `other` → cautious refusal or deflection.

### Skills Gap Map (Pair D)

Consumes `dbo.cohort_gap_cache`. If the table is empty, the page should show an informative "no data" state, not a crash.

**Verification:**
- If you ran `api_smoke.py` in Step 2, the cache is already populated. Otherwise trigger it manually:

  **bash:**
  ```bash
  curl -X POST http://127.0.0.1:8000/analytics/triggers/cohort_gap_analysis -H "Content-Type: application/json" -d '{"cohort_key":"demo-cohort"}'
  ```

  **PowerShell:**
  ```powershell
  Invoke-RestMethod -Uri http://127.0.0.1:8000/analytics/triggers/cohort_gap_analysis -Method POST -ContentType "application/json" -Body '{"cohort_key":"demo-cohort"}'
  ```

- Refresh the page; the cached cohort row's `gap_data.market_skill_demand` should render as a bar/heat chart.

### Emergence Alerts (Pair C, data from Pair A)

Consumes `dbo.disruption_fingerprints` LEFT JOIN `dbo.canonical_roles` on `cr.role_id = df.canonical_role_id`. Shows all disruption fingerprints (not just Emergence-tagged ones). If the fingerprints table is empty, the page shows a "no data" state.

**Verification:**
- Refresh fingerprints (§4 Step 2) first.
- Page should show a table with one row per canonical role, columns: Role, Disruption category, AI skill density, Posting growth, Employer count, Trajectory, AI intensity trend, No pre-ChatGPT baseline.
- With single-temporal-period data (all postings from the same era), `Disruption category` will show "—" (empty) and `Trajectory` will show "stable" for all roles. This is expected — see Troubleshooting §11.
- With multi-period data, expect category badges (Displacement / Augmentation / Transformation / Emergence) and varying trajectories.

### Regional Heatmap (Pair D)

Consumes `dbo.geo_demand_weekly`. Requires at least 1+ rows with `borderplex_subregion`/`posting_count`. Expected subregion values: `el_paso`, `las_cruces`, `ciudad_juarez`, `regional`.

**Verification:**
- If `geo_demand_weekly` is empty, page shows "No rows in geo_demand_weekly yet" — run the Week 7 geo-demand aggregation step first (see §9 Prerequisite).
- With data: Plotly heatmap renders with subregion labels and a temporal share-of-demand line chart below it.
- Coverage check at the bottom reports which of the 4 expected Borderplex buckets have data.

---

## 10. Cost Tracking

Cost tracking is a core JIE requirement. Every LLM call is priced at runtime using the `PRICING` dict in `common/llm_adapter.py`, and logged to `dbo.llm_audit_log`. The `CostLedger` in `analytics/query_engine/ledger_utils.py` accumulates costs across all Q&A legs so each `SynthesisResponse` carries a `cost_breakdown_usd` showing exactly what was spent per pipeline stage. These numbers come from real token counts, not estimates.

There are 3 cost surfaces to be aware of:
- **Developer generation cost** — tokens consumed by Cursor / Claude Code during development. Not tracked by the pipeline; monitored via each developer's IDE billing.
- **Runtime inference cost** — per-query LLM calls priced by the adapter's `PRICING` dict. This is what the pipeline tracks in `dbo.llm_audit_log` and surfaces in `cost_breakdown_usd`.
- **Context window cost** — tokens loaded into `_SCHEMA_HINT`, few-shot examples, and system prompts. Fixed per deployment; affects the per-call cost but is not separately itemized.

### Step 1 — Inspect a `SynthesisResponse.cost_breakdown_usd`

Already covered in §5 Step 2. Expected leg keys: `intent_classification`, `sql_generation`, `synthesis`, `follow_up`.

Typical per-turn cost on `chat-gpt41mini` + `chat-gpt41`:

| Leg | Tier / deployment | Rough cost per call |
|-----|-------------------|---------------------|
| intent_classification | Haiku / `chat-gpt41mini` | ~$0.0002–0.0005 |
| sql_generation | Haiku / `chat-gpt41mini` | ~$0.0005–0.0015 |
| synthesis | Sonnet / `chat-gpt41` | ~$0.008–0.015 |
| follow_up | Haiku / `chat-gpt41mini` | ~$0.0005–0.0010 |
| **Total per turn** | — | **~$0.010–0.020** |

### Step 2 — Confirm `dbo.llm_audit_log` rows match

Every adapter call in the Q&A path writes a row via `log_extraction_event`:

```bash
python scripts/db_check.py query "SELECT agent_name, model, provider, input_tokens, output_tokens, ROUND(cost_usd::numeric, 5) AS cost_usd, created_at FROM dbo.llm_audit_log WHERE agent_name LIKE 'analytics-qna-%' ORDER BY created_at DESC LIMIT 10"
```

**Expected:**
- `agent_name` values: `analytics-qna-intent`, `analytics-qna-sql`, `analytics-qna-synthesis`, `analytics-qna-followup`, `analytics-intent-classification`.
- `model` matches the resolved deployment (e.g. `chat-gpt41mini`, `chat-gpt41`).
- `provider='azure_openai'` on real calls.
- Token counts and `cost_usd` are non-zero on real calls.

### Step 3 — Verify `resolve_model_tier` maps deployments correctly

The `resolve_model_tier()` function in `common/llm_adapter.py` maps Azure deployment names (e.g., `chat-gpt41mini`) to the canonical pricing tier keys used in the `PRICING` dict. If this mapping is misconfigured, costs are over- or under-counted. The 8.6x overcounting bug fixed in PR #142 was caused by a deployment name resolving to the wrong tier.

```
python scripts/smoke/cost_model_tier_check.py
```

**What the script does** (core body — same code in `scripts/smoke/cost_model_tier_check.py`):

```python
from common.llm_adapter import resolve_model_tier, PRICING
for dep in ('chat-gpt41mini', 'chat-gpt41', 'chat-gpt4o-mini', 'claude-sonnet-4-5'):
    tier = resolve_model_tier(dep)
    pricing = PRICING.get(tier, {})
    print(f'{dep:25} -> {tier:20} input=${pricing.get("input", "?")}/1M  output=${pricing.get("output", "?")}/1M')
```

**Expected:** `chat-gpt41mini → gpt-4.1-mini`, `chat-gpt41 → gpt-4.1`, each with populated input/output pricing per million tokens. If any deployment prints `?` for pricing, the tier mapping is broken — check `resolve_model_tier` in `common/llm_adapter.py`.

### Step 4 — Daily cost roll-up (demo-day hygiene)

```bash
python scripts/db_check.py query "SELECT DATE(created_at) AS day, agent_name, COUNT(*) AS calls, ROUND(SUM(cost_usd)::numeric, 4) AS total_usd FROM dbo.llm_audit_log WHERE agent_name LIKE 'analytics-qna-%' GROUP BY DATE(created_at), agent_name ORDER BY day DESC, total_usd DESC LIMIT 20"
```

Run this before and after the demo to get an actual demo-day cost number for the end-of-week wrap-up.

---

## 11. Troubleshooting

### LLM / Routing

**`ValueError: No LLM deployment configured. Set LLM_DEFAULT or LLM_{ROLE} in your .env.`**

Root cause: after the PR #155 routing refactor, the adapter fails fast when neither `LLM_{ROLE}` nor `LLM_DEFAULT` is set. Copy the LLM block from `.env.example`:

```bash
LLM_PROVIDER=azure_openai
LLM_DEFAULT=chat-gpt41mini
LLM_SYNTHESIS=chat-gpt41
```

**Rate limit / 429 on `chat-gpt41` during a live demo**

Azure's Sonnet-tier deployment is 5 RPM / 5K TPM on our contract. If the demo audience is rapid-firing questions, you will hit the ceiling within the first 3 turns. Mitigations:

- Pre-warm the cache before the demo (one fresh call per planned question earlier in the session).
- Stagger demo runs at least 20 seconds apart.
- Temporarily route synthesis to the Haiku tier (`LLM_SYNTHESIS=chat-gpt41mini`) for stress tests — answer quality drops but you unblock.

**`classify_workforce_question` returns `intent="other"` on every call**

The function's LLM-failure path returns `{"intent": "other", "confidence": 0.0, "needs_clarification": True}`. If you are getting this on every call, the underlying LLM call is failing:

1. Check `LLM_DEFAULT` is set (`env | grep LLM_DEFAULT`).
2. Override with `LLM_CLASSIFICATION=chat-gpt41mini` to route explicitly.
3. Tail the logs for `intent_classification_llm_exception` — shows the underlying exception type.
4. Verify Azure connectivity: `python scripts/test_llm_connection.py` (Week 4 utility).

### Disruption Fingerprints

**Empty `dbo.disruption_fingerprints` after refresh**

- Confirm `dbo.canonical_roles` has rows — the service iterates `repository.fetch_canonical_roles()`. No roles = zero fingerprints.
- Confirm temporal period snapshots exist — sparse roles are still fingerprinted (all 4 periods normalized with `has_observed_data=False`), so an empty table after refresh means the role list itself was empty.

**Content fingerprints change between runs with the same upstream data**

Check that `build_fingerprint_hash_material` sorts categories deterministically and that `skill_mix` / `tool_mix` / `task_mix` are sorted by key inside `build_fingerprint_hash_material`. Non-determinism here indicates a regression in the Pair A sort order.

**`DisruptionRefreshed` event not being emitted**

The bus is optional — the service only publishes when a bus is registered via `register_disruption_refreshed_bus(bus)` or passed to the constructor (`DisruptionFingerprintService(event_bus=bus)`). Default instantiation silently skips emission. For the demo, explicitly register a bus (see §4 Step 5).

### SQL Guardrails

**Valid query gets rejected by `validate_sql`**

`ALLOWED_TABLES` in `analytics/query_engine/sql_guardrails.py` is a frozenset of approved aggregate tables (`skill_demand_weekly`, `tool_demand_weekly`, `role_snapshot_weekly`, `sector_summary_weekly`, `geo_demand_weekly`, `skill_velocity`, `skill_co_occurrence`, `posting_freshness`, `trajectory_map`, `analytics_pipeline_state`, `cohort_gap_cache`, `canonical_roles`). If you added a new table, update this set.

For the Ask-the-Data path (`validate_ask_the_data_sql`), the allowlist is `ASK_THE_DATA_ALLOWED_TABLES`: `job_postings`, `companies`, `company_addresses`, `skills`, `technology_areas`, `industry_sectors`, `analytics_aggregates`, `normalized_jobs`, `raw_ingested_jobs`. These are intentionally **different** sets — triggers query aggregates, Ask-the-Data queries operational tables.

**`LIMIT` rewritten to 100 unexpectedly**

Both guardrails cap rows at `MAX_ROWS=100`. This is by design — a hallucinated `LIMIT 1000000` gets safely capped rather than rejected. If you need a higher cap for a legitimate use case, update `MAX_ROWS` and `_ROW_LIMIT` together.

### FastAPI

**OpenAPI `/docs` 404s**

`analytics/api/app.py` creates the FastAPI app and mounts `analytics_router`. If `/docs` returns 404:

1. Confirm you started via `PYTHONPATH=. python scripts/run_analytics_api.py` (sets `sys.path` to the repo root before import).
2. Confirm the route was registered: `curl -sS http://127.0.0.1:8000/openapi.json | python -m json.tool | grep -E '"/analytics/|"paths"'`.
3. If the `paths` list is empty, `analytics.api.routes` failed to import — check stderr for an `ImportError`.

**`HTTPException 504 query_timeout` on trigger calls**

`execute_validated_query` sets `SET LOCAL statement_timeout = 30000` (30s). Triggers run plain SELECTs — a 30s timeout indicates a missing index or a full table scan on a large table. Check `EXPLAIN` on the generated SQL.

### Streamlit

**Ask-the-Data page shows "Using fixture data (JSON)" banner**

`PYTHON_DATABASE_URL` (or `PYTHON_DATABASE_URL_READONLY`) is not set in the Streamlit process's environment. Export it before running `streamlit run`, or add it to a `.env` that `load_repo_root_dotenv()` picks up from the repo root.

**Emergence Alerts page empty even though fingerprints exist**

The page joins `disruption_fingerprints` to `canonical_roles` on `cr.role_id = df.canonical_role_id` (LEFT JOIN). If role labels show as `None`, the `role_id` values in `canonical_roles` do not match the `canonical_role_id` values in `disruption_fingerprints`. Confirm with:

```bash
python scripts/db_check.py query "SELECT COUNT(*) AS fp_rows FROM dbo.disruption_fingerprints"
python scripts/db_check.py query "SELECT COUNT(*) AS joined FROM dbo.disruption_fingerprints df JOIN dbo.canonical_roles cr ON cr.role_id = df.canonical_role_id"
```

If `fp_rows > joined`, the role ids are out of sync — fix in Pair A repository or re-seed `canonical_roles`.

**Emergence Alerts shows all categories as "—" (empty)**

When all postings come from the same temporal period (e.g., all recent JSearch ingestions land in `agentic_era`), the disruption classifier returns `disruption_category=[]` for every role. This is expected — the classifier compares skill/tool/task mix across 4 temporal eras, and with only one period the deltas are all zero (the 30% skill-change threshold is never met). Seed multi-period test data to see non-empty categories.

**All Week 8 pages show "no data" after running `api_smoke.py`**

The API triggers (`cohort_gap_analysis`, `role_benchmark`, `emerging_skills_scan`, `custom_employer_comparison`) query the Week 7 aggregate tables internally. If those tables are empty, the triggers succeed (HTTP 200) but write stub/empty data to their cache tables. The Streamlit pages then render the empty cache data as "no data" states.

Fix: run the aggregate population commands in §9 Prerequisite (Steps A and B), then re-run `api_smoke.py` to re-populate the caches with real data.

**Regional Heatmap map tile missing**

The page uses Plotly heatmap from `dbo.geo_demand_weekly`. If the map is blank but the table shows rows, the `borderplex_subregion` values may not match the expected lookup (`el_paso`, `las_cruces`, `ciudad_juarez`, `regional`). Run:

```bash
python scripts/db_check.py query "SELECT DISTINCT borderplex_subregion FROM dbo.geo_demand_weekly"
```

Unexpected subregion values indicate a Pair A Borderplex classification drift — see Week 7 runbook §5 Table 5.

**Regional Heatmap page completely empty**

`dbo.geo_demand_weekly` is populated by the Week 7 analytics geo-demand aggregation step. If the table has zero rows, the aggregation has not been run. Run the Week 7 aggregate pipeline (see §9 Prerequisite).

### Known post-merge gaps surfaced during the 2026-04-16 smoke test

**`migration_orchestration_audit_skipped` warning on every `run_migrations()` call**

`common/data_store/migrations.py` line 513 tries to create an index on `event_type`, but the ORM model in `common/data_store/models.py::OrchestrationAuditLog` uses `endpoint` as the column name. The index creation fails with `column "event_type" does not exist` and gets logged as a warning. Audit rows still write fine — the index is just missing.

**Fix (deferred to Phase 7 hardening):** rename the migration's index target from `event_type` to `endpoint`, OR add an `event_type` column to the ORM model if that was the original intent. Pair D owns the fix.

**`sqlglot` / `fastapi` / `uvicorn` not installed on older venvs**

Venvs created before PR #161 merged do not have the Week 8 hard deps. The first time you import any module under `analytics/query_engine/`, you will see:

```
ModuleNotFoundError: No module named 'sqlglot'
```

Fix: `pip install -r requirements.txt` to pick up `sqlglot>=25.0`, `fastapi>=0.115`, `uvicorn[standard]>=0.30`. See §2 "Install Week 8 dependencies" for the exact commands.

### Audit Log

**`dbo.orchestration_audit_log` empty after Q&A calls**

The happy-path handler writes audit rows inside the `session_scope()` transaction. If you see Q&A responses in the browser but no audit rows, check:

1. `dbo.orchestration_audit_log` exists: Week 8 migration in `common/data_store/migrations.py`.
2. The session actually committed — the route handler returns before the commit only if an exception escaped the `session_scope()` block.
3. Look for `sql_execute_failed` in structured logs — execution failure before audit insert is a known follow-up issue.

**`question_hash` or `sql_hash` NULL when the call succeeded**

These fields are optional (nullable). They are populated from `audit_log.question_hash()` / `audit_log.sql_hash()`. If they are NULL despite a successful call, the route handler passed `None` for `question` or `sql_generated` — check that the handler invokes `insert_orchestration_audit` with the right arguments.

### Surfaced during Week 8 runbook walkthrough (2026-04-16)

**#186 — `_SCHEMA_HINT` missing columns cause LLM hallucination**

The LLM hallucinated `skill_id`, `posted_date`, `company_id` across 3 reproductions. These columns do not exist in the aggregate tables referenced by `_SCHEMA_HINT`. The refusal path fires correctly — synthesis is skipped and no fabricated data is returned — but happy-path answers are blocked until the schema hint is corrected. Affects: §0 Step 4, §5 Step 2, §5 Step 5, §6 Q&A endpoint, §9 Ask-the-Data page.

**#187 — Audit writes happen only at the API route-handler layer**

Direct function calls (`scripts/smoke/qa_pipeline.py`, `sql_guardrails_adversarial.py`) do not write audit rows because they bypass the FastAPI route handlers. Only HTTP requests that hit the endpoints in `analytics/api/routes.py` are audited. This means smoke scripts that call pipeline functions directly will show zero audit rows for their correlation IDs. Not a bug — the audit integration point is intentionally at the API boundary.

**#188 — `success=true` on refused Q&A does not mean the answer was good**

The `success` field in `dbo.orchestration_audit_log` reflects the HTTP status code (200 = `true`, 4xx/5xx = `false`). A row with `success=true, confidence=0.0` is a quality failure (the question was refused or the evidence was insufficient), not a system failure. When reviewing audit logs, check `confidence` and `error_code` alongside `success` to distinguish system health from answer quality.

**Migration `event_type` index warning — column mismatch**

`common/data_store/migrations.py` tries to index `event_type` but the ORM model column is `endpoint`. The migration logs a warning (`column "event_type" does not exist`) but does not fail — audit rows write fine, the index is just missing. Deferred fix for Pair D.

**`df.id` column missing in Emergence Alerts page (FIXED)**

`dashboard/pages_emergence_alerts.py` originally referenced `df.id` in the SQL query, but `dbo.disruption_fingerprints` has no `id` column — only `canonical_role_id` as the row identifier. This caused a hard crash (`UndefinedColumn: column df.id does not exist`) whenever the page loaded. Fixed by removing the `df.id` select/order-by and using `df.canonical_role_id` instead. Also removed the `WHERE` filter on Emergence patterns — with single-temporal-period data all `disruption_category` values are empty arrays, so the filter dropped every row.

**Week 7 aggregate tables all empty — Streamlit pages show no data**

If `skill_demand_weekly`, `tool_demand_weekly`, `role_snapshot_weekly`, `sector_summary_weekly`, `skill_velocity`, `skill_co_occurrence`, `geo_demand_weekly`, and `trajectory_map` all have zero rows, the Week 7 analytics aggregate pipeline has not been run. This is the root cause of most "no data" states on Week 8 Streamlit pages. The `cohort_gap_cache` may have rows from `api_smoke.py`, but those rows contain empty `market_skill_demand` arrays because the triggers queried empty upstream tables.

Fix: run `python scripts/refresh_aggregates.py`, then re-run `api_smoke.py` to refresh caches with real data. If `sector_summary_weekly` and `geo_demand_weekly` are still empty after that, the root cause is [#172](https://github.com/Building-With-Agents/job-intelligence-engine/issues/172) — `job_postings.publish_date` is 99% NULL because `date_posted` from JSearch was never promoted from `normalized_jobs`. Once #172 merges and the backfill runs, re-run the aggregates per [#189](https://github.com/Building-With-Agents/job-intelligence-engine/issues/189).

**`sqlglot` / `fastapi` / `uvicorn` missing on pre-merge venvs**

Venvs created before the Week 8 PRs merged are missing these dependencies. Any import of `analytics/query_engine/` modules will fail with `ModuleNotFoundError`. Fix: `pip install -r requirements.txt` after pulling the merged Week 8 branches.

**Single-temporal-period data produces empty disruption categories**

When all postings come from the same temporal era (e.g., all recent JSearch ingestions land in `agentic_era`), the disruption classifier returns `cats=[]` for every role. This is expected behavior — the classifier compares skill/tool/task mix across `pre_chatgpt` → `early_genai` → `post_gpt4` → `agentic_era`, and with only one period the deltas are all zero. The 30% skill-change threshold is never met, so no patterns fire. Not a bug — seed multi-period test data to see non-empty categories.

---

## Appendix — Week 8 file map

For the demo day debrief:

| Workstream | Key paths |
|------------|-----------|
| Pair A — Disruption | `analytics/disruption/{models,service,classifier,repository}.py`, `common/events/disruption_refreshed.py`, `dbo.disruption_fingerprints` table |
| Pair B — Intent + Router | `analytics/query_engine/{intent,router,routing}.py` |
| Pair C — Evidence + Synthesis + Ask-the-Data UI | `analytics/query_engine/{qna,evidence,synthesis,grounding,schemas,constants,ledger_utils}.py`, `dashboard/pages_ask_the_data.py` |
| Pair D — API + Guardrails + Audit + 3 viz pages | `analytics/api/{app,routes,schemas,deps}.py`, `analytics/query_engine/{sql_guardrails,execute_safe,audit_log}.py`, `scripts/run_analytics_api.py`, `dashboard/pages_{skills_gap_map,emergence_alerts,regional_heatmap}.py`, `dbo.{orchestration_audit_log,cohort_gap_cache}` tables |

For upstream pipeline details (ingestion through Week 7 analytics aggregates), see [WEEK07_TESTING_RUNBOOK.md](WEEK07_TESTING_RUNBOOK.md).
