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
4. Week 7 aggregate tables are populated (see Week 7 runbook Section 5 if they are empty).

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

If `canonical_roles` or any Week 7 aggregate is `(empty)`, run the Pair C clustering flow and Pair A aggregates refresh in the Week 7 runbook before continuing.

If any Week 8 table shows `MISSING`, run migrations per §2 above.

If any of these are empty, refresh the Week 7 aggregates first (see Week 7 runbook Section 5, Pair A's `verify_aggregates.py`).

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

### Step 2 — Run a full refresh

```bash
PYTHONPATH=. python -c "
from analytics.disruption.service import DisruptionFingerprintService
from common.data_store.database import session_scope
svc = DisruptionFingerprintService()
with session_scope() as s:
    result = svc.refresh_disruption_fingerprints(
        session=s,
        correlation_id='wk8-disruption-demo',
    )
    print(f'roles_considered={result.roles_considered}')
    print(f'computed_count={result.computed_count}')
    for fp in result.fingerprints[:3]:
        print(f'  role={fp.canonical_role_id}  cats={fp.disruption_category}  ai_trend={fp.ai_intensity_trend}  wrs={fp.workflow_restructuring_score:.3f}')
"
```

**Expected output:**

- `roles_considered` equals the row count in `dbo.canonical_roles`.
- `computed_count` equals `roles_considered` (every role gets a fingerprint row, including sparse ones).
- For each printed role: `cats` is a non-empty list (Displacement / Augmentation / Transformation / Emergence — a role may carry multiple labels); `ai_trend` is one of `increasing`/`decreasing`/`stable`; `wrs` is a float in `[0, 1]`.

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

**Expected:** All four labels (`Displacement`, `Augmentation`, `Transformation`, `Emergence`) appear with non-zero counts. If one is missing, the classifier thresholds in `analytics/disruption/classifier.py` may not be triggering on your dataset — flag to Pair A.

### Step 5 — Verify `DisruptionRefreshed` event emission

The service publishes `DisruptionRefreshed` only when a bus is registered. To test emission, attach an in-process capture bus:

```bash
PYTHONPATH=. python -c "
from analytics.disruption.service import DisruptionFingerprintService
from common.data_store.database import session_scope

class CaptureBus:
    def __init__(self):
        self.events = []
    def publish(self, envelope):
        self.events.append(envelope)

bus = CaptureBus()
svc = DisruptionFingerprintService(event_bus=bus)
with session_scope() as s:
    svc.refresh_disruption_fingerprints(session=s, correlation_id='wk8-event-check')
assert bus.events, 'expected at least one DisruptionRefreshed envelope'
env = bus.events[0]
print('event_type:', env.event_type)
print('correlation_id:', env.correlation_id)
print('payload.role_count:', env.payload.role_count)
print('payload.displacement_count:', env.payload.displacement_count)
print('payload.augmentation_count:', env.payload.augmentation_count)
print('payload.transformation_count:', env.payload.transformation_count)
print('payload.emergence_count:', env.payload.emergence_count)
print('payload.refresh_duration_ms:', env.payload.refresh_duration_ms)
"
```

**Expected:** `event_type='DisruptionRefreshed'`, `correlation_id` matches what you passed, the four category counts sum to at least `role_count` (a role with multiple labels increments multiple buckets), and `refresh_duration_ms >= 0`.

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

```bash
PYTHONPATH=. python -c "
from analytics.query_engine.intent import classify_workforce_question
r = classify_workforce_question(
    'Which welding skills are growing fastest in El Paso over the last 90 days?',
    correlation_id='wk8-intent-demo',
)
import json; print(json.dumps(r, indent=2))
"
```

**Expected output shape:**

```json
{
  "intent": "trend",
  "confidence": 0.85,
  "needs_clarification": false,
  "extracted_entities": {
    "geographic_terms": ["El Paso"],
    "role_names": [],
    "skill_names": ["welding"],
    "time_references": ["last 90 days"]
  }
}
```

**What to check:**
- `intent` is one of the 10 allowed labels: `trend | role_evolution | disruption | emergence | curriculum | employer | workflow | geographic | comparison | other`.
- `confidence` is a float in `[0, 1]`. `needs_clarification` is `true` only when `confidence < 0.55`.
- `extracted_entities` always contains the 4 keys, even if lists are empty.
- The call went through `common.llm_adapter.complete` with `role="classification"` (Haiku-tier / `chat-gpt41mini` via `LLM_DEFAULT`).

### Step 2 — Full guardrailed routing (end-to-end happy path)

```bash
PYTHONPATH=. python -c "
from analytics.query_engine.routing import run_guardrailed_analytics_query
from common.data_store.database import session_scope
from common.types.query_request import QueryRequest

q = 'Which 10 skills had the highest posting counts in the most recent week?'
req = QueryRequest(query=q)
with session_scope() as s:
    resp = run_guardrailed_analytics_query(req, session=s, correlation_id='wk8-qa-happy')

print('--- ANSWER ---')
print(resp.answer_text)
print()
print('--- CITATIONS (first 3) ---')
for c in resp.citations[:3]:
    print(f'  [{c.citation_id}] {c.summary} (table={c.source_table}, n={c.supporting_count}, period={c.time_period})')
print()
print('periods_described:', resp.periods_described)
print('confidence:', resp.confidence, 'flagged_low:', resp.confidence_flagged_low)
print('volume_flagged_low:', resp.volume_flagged_low)
print('refused:', resp.refused)
print('follow_up_questions:', resp.follow_up_questions)
print('total_cost_usd:', round(resp.total_cost_usd, 5))
print('cost_breakdown_usd:', resp.cost_breakdown_usd)
"
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

### Step 3 — Inspect SQL guardrails in isolation

```bash
PYTHONPATH=. python -c "
from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql, validate_sql

ok, reason, sql = validate_ask_the_data_sql(
    'SELECT skill_label, posting_count FROM dbo.skill_demand_weekly ORDER BY posting_count DESC LIMIT 50'
)
print('ask_the_data ok:', ok, 'reason:', reason)
print('normalized_sql:', sql)
print()
res = validate_sql('SELECT skill_label, posting_count FROM dbo.skill_demand_weekly LIMIT 500')
print('validate_sql ok:', res.ok, 'reason:', res.reason)
print('sql_for_execution:', res.sql_for_execution)
"
```

**Expected:**
- `validate_ask_the_data_sql`: `ok=True`, `reason='ok'`, `normalized_sql` ends with `LIMIT 50` (cap is 100 — requested 50, kept).
- `validate_sql`: `ok=True`, `reason=None`, and the returned `sql_for_execution` has `LIMIT 100` (the 500 was capped to `MAX_ROWS=100`).

### Step 4 — Verify `execute_safe` enforces the 30s timeout

Every Q&A path and every trigger runs through `execute_validated_query()`, which issues `SET LOCAL statement_timeout = 30000` before running the SELECT. Timeouts surface as `RuntimeError("query_timeout")`.

```bash
PYTHONPATH=. python -c "
from analytics.query_engine.execute_safe import execute_validated_query
from common.data_store.database import session_scope
with session_scope() as s:
    rows, n = execute_validated_query(s, 'SELECT 1 AS ping')
    print('rows:', rows, 'count:', n)
"
```

**Expected:** `rows=[{'ping': 1}]`, `count=1`. Exceptions for timeouts or DB failures are translated to `query_timeout` / `query_execution_failed` — the raw driver message is logged but not surfaced to the caller.

### Step 5 — Check grounding retry path (hallucination guard)

Synthesis uses a two-prompt retry when the first answer mentions numbers not present in the citations. To observe the path, run a question that stretches the evidence:

```bash
PYTHONPATH=. python -c "
from analytics.query_engine.routing import run_guardrailed_analytics_query
from common.data_store.database import session_scope
from common.types.query_request import QueryRequest
req = QueryRequest(query='What is the median salary for welders in El Paso in Q1 2025?')
with session_scope() as s:
    resp = run_guardrailed_analytics_query(req, session=s, correlation_id='wk8-grounding')
print('answer_len:', len(resp.answer_text))
print('refused:', resp.refused, 'msg:', resp.refusal_message)
print('confidence:', resp.confidence, 'flagged_low:', resp.confidence_flagged_low)
print('volume_flagged_low:', resp.volume_flagged_low)
"
```

**What to check:** If the evidence is thin, `confidence_flagged_low` or `volume_flagged_low` is `true` and `answer_text` is short / cautious (not hallucinated numbers). If evidence is absent entirely, `refused=True` with a non-empty `refusal_message`.

---

## 6. FastAPI API Smoke Test (Pair D)

### Step 1 — Start the API

```bash
PYTHONPATH=. python scripts/run_analytics_api.py
```

**Expected console output:**

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

Leave this running in one terminal; issue the curl commands below from another.

### Step 2 — Verify OpenAPI `/docs` loads

Open http://127.0.0.1:8000/docs in a browser.

**Expected:** Swagger UI lists all five endpoints under the `analytics` tag:
- `POST /analytics/query`
- `POST /analytics/triggers/cohort_gap_analysis`
- `POST /analytics/triggers/role_benchmark`
- `POST /analytics/triggers/emerging_skills_scan`
- `POST /analytics/triggers/custom_employer_comparison`

The raw JSON schema is at http://127.0.0.1:8000/openapi.json.

### Step 3 — `POST /analytics/query` — Q&A happy path

```bash
curl -sS -X POST http://127.0.0.1:8000/analytics/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Which 5 skills have the highest posting counts?","correlation_id":"api-smoke-1"}' | python -m json.tool
```

**Expected response (shape):**

```json
{
  "answer": "...grounded narrative...",
  "evidence": [
    {"title": "c1", "source": "skill_demand_weekly", "snippet": "...", "supporting_count": 42, "time_period": "..."}
  ],
  "confidence": 0.75,
  "periods_described": "...",
  "confidence_flagged_low": false,
  "confidence_explanation": null,
  "volume_flagged_low": false,
  "volume_warning": null,
  "refused": false,
  "refusal_message": null,
  "sql_execution_error_detail": null,
  "follow_up_questions": ["...", "...", "..."],
  "sql_generated": "SELECT ...",
  "cost_usd": 0.0123,
  "total_cost_usd": 0.0123,
  "cost_breakdown_usd": {"intent_classification": 0.0005, "sql_generation": 0.0011, "synthesis": 0.0100, "follow_up": 0.0007}
}
```

### Step 4 — Each of the 4 triggers

```bash
# Cohort gap (week_start=null returns latest rows)
curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/cohort_gap_analysis \
  -H "Content-Type: application/json" \
  -d '{"cohort_key":"demo-cohort","week_start":null}' | python -m json.tool

# Role benchmark (replace role_1 with a real canonical_role_id from dbo.canonical_roles)
curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark \
  -H "Content-Type: application/json" \
  -d '{"canonical_role_id":"role_1","week_start":null}' | python -m json.tool

# Emerging skills
curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/emerging_skills_scan \
  -H "Content-Type: application/json" \
  -d '{"week_start":null,"min_posting_count":5}' | python -m json.tool

# Custom employer comparison (company_id can be any safe token for now; returns market context)
curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/custom_employer_comparison \
  -H "Content-Type: application/json" \
  -d '{"company_id":"company_42","week_start":null}' | python -m json.tool
```

**Expected `TriggerEnvelope` shape for each:**

```json
{
  "trigger": "cohort_gap_analysis",
  "cached": false,
  "computed_at": "2026-04-16T12:34:56.789012+00:00",
  "data": { "cohort_key": "...", "week_start": null, "market_skill_demand": [ ... ] }
}
```

### Step 5 — Confirm the 24-hour cache works

Rerun the same `cohort_gap_analysis` call immediately:

```bash
curl -sS -X POST http://127.0.0.1:8000/analytics/triggers/cohort_gap_analysis \
  -H "Content-Type: application/json" \
  -d '{"cohort_key":"demo-cohort","week_start":null}' | python -m json.tool
```

**Expected:** `"cached": true`, same `computed_at` timestamp as the first call, same `data` payload.

Check the cache table directly:

```bash
python scripts/db_check.py query "SELECT trigger_type, cohort_key, computed_at, expires_at FROM dbo.cohort_gap_cache ORDER BY computed_at DESC LIMIT 5"
```

`expires_at` should be ~24 hours after `computed_at`.

---

## 7. SQL Guardrails — Adversarial Verification

With the API still running, throw adversarial payloads at `/analytics/query` and confirm each is rejected at the guardrail before execution.

> **Note:** The guardrail operates on LLM-generated SQL, not on user questions. Adversarial user questions route through the Haiku-tier SQL generator first — the generator is prompted to produce only allow-listed SELECTs, and the guardrail is the hard backstop.

### Step 1 — Directly validate known-bad SQL strings

Easier to exercise the guardrail code path directly (bypasses LLM non-determinism):

```bash
PYTHONPATH=. python -c "
from analytics.query_engine.sql_guardrails import validate_ask_the_data_sql, validate_sql

cases_atd = [
    ('DROP TABLE', 'DROP TABLE dbo.job_postings'),
    ('INSERT',     'INSERT INTO dbo.job_postings (id) VALUES (1)'),
    ('UNION over disallowed', 'SELECT 1 UNION SELECT * FROM pg_shadow'),
    ('multi-statement', 'SELECT 1; SELECT 2'),
    ('comment bypass', 'SELECT * FROM dbo.job_postings -- ; DROP TABLE x'),
    ('subquery forbidden table', 'SELECT * FROM dbo.job_postings WHERE id IN (SELECT id FROM pg_authid)'),
    ('disallowed schema', 'SELECT * FROM pg_catalog.pg_tables'),
    ('disallowed table', 'SELECT * FROM dbo.llm_audit_log'),
    ('not SELECT', 'TRUNCATE TABLE dbo.job_postings'),
]
for name, sql in cases_atd:
    ok, reason, norm = validate_ask_the_data_sql(sql)
    print(f'[ask_the_data] {name!r:30} ok={ok!s:5} reason={reason}')

print()
cases_agg = [
    ('DROP in aggregate',    'DROP TABLE dbo.skill_demand_weekly'),
    ('INSERT in aggregate',  'INSERT INTO dbo.skill_demand_weekly (id) VALUES (1)'),
    ('UPDATE forbidden',     'UPDATE dbo.skill_demand_weekly SET posting_count=0'),
    ('multi-statement',      'SELECT 1; SELECT 2'),
    ('non-allowlisted table','SELECT * FROM dbo.job_postings'),
    ('LIMIT over cap',       'SELECT skill_label FROM dbo.skill_demand_weekly LIMIT 5000'),
]
for name, sql in cases_agg:
    res = validate_sql(sql)
    print(f'[aggregate]    {name!r:30} ok={res.ok!s:5} reason={res.reason!s:40} sql={res.sql_for_execution[:80] if res.sql else \"\"}')
"
```

**Expected:**

- Every `ask_the_data` case returns `ok=False` with a specific `reason`: `forbidden_keyword`, `multiple_statements`, `disallowed_schema:pg_catalog`, `disallowed_table:llm_audit_log`, `disallowed_table:pg_authid`, `disallowed_table:pg_shadow`, `not_select`.
- Every `aggregate` case returns `ok=False` **except** the `LIMIT over cap` case, which returns `ok=True` with `sql_for_execution` rewritten to `LIMIT 100` (cap normalized, not rejected).

### Step 2 — Confirm each rejection is logged

Every validation call (pass or fail) writes a zero-token audit row to `dbo.llm_audit_log` via `log_sql_validation_to_llm_audit`:

```bash
python scripts/db_check.py query "SELECT agent_name, model, provider, success, error_reason, created_at FROM dbo.llm_audit_log WHERE agent_name='analytics_query_api' ORDER BY created_at DESC LIMIT 10"
```

**Expected:** `agent_name='analytics_query_api'`, `model='sql-guardrail'`, `provider='internal'`, `success=false` for rejections with `error_reason` matching the validation reason.

### Step 3 — End-to-end via the API

Fire one rejection through the full HTTP path to make sure the rejection travels back as a clean 4xx (trigger endpoints raise `HTTPException 400` on `ValueError` from `validate_sql`):

```bash
# Force a rejection through the trigger path — cohort_gap_analysis hardcodes SQL, so instead
# hit role_benchmark with an invalid canonical_role_id to exercise the _require_safe_token guard.
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark \
  -H "Content-Type: application/json" \
  -d '{"canonical_role_id":"bobby; DROP TABLE students--"}'
```

**Expected:** `HTTP 400`. Response body has `{"detail": "invalid_role_id"}`.

---

## 8. Audit Log Verification

### Step 1 — Confirm table and columns

```bash
python scripts/db_check.py query "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_schema='dbo' AND table_name='orchestration_audit_log' ORDER BY ordinal_position"
```

**Expected columns:** `id`, `created_at`, `correlation_id`, `endpoint`, `question_hash`, `sql_hash`, `confidence`, `success`, `error_code`, `payload`.

### Step 2 — Run a few Q&A calls with distinct correlation IDs

```bash
for cid in audit-1 audit-2 audit-3; do
  curl -sS -o /dev/null -X POST http://127.0.0.1:8000/analytics/query \
    -H "Content-Type: application/json" \
    -d "{\"question\":\"Top skills in the most recent week\",\"correlation_id\":\"$cid\"}"
done
```

### Step 3 — Verify audit rows landed with populated fields

```bash
python scripts/db_check.py query "SELECT created_at, correlation_id, endpoint, LENGTH(question_hash) AS qh_len, LENGTH(sql_hash) AS sh_len, confidence, success, error_code FROM dbo.orchestration_audit_log ORDER BY created_at DESC LIMIT 5"
```

**What to check:**
- `correlation_id` is one of the IDs you passed (`audit-1`, `audit-2`, `audit-3`).
- `endpoint` is `/analytics/query` for Q&A calls, `/analytics/triggers/<name>` for trigger calls.
- `qh_len = 64` (SHA-256 hex of the question).
- `sh_len = 64` (SHA-256 hex of the generated SQL).
- `confidence` is a float; `success = true` on happy paths.
- `error_code` is `NULL` on success.

### Step 4 — Failure-path audit (SQL rejection → audit row with error_code)

Force a failure and confirm the failure is captured:

```bash
# invalid canonical_role_id triggers ValueError before SQL is generated — exercises the 400 path
curl -sS -o /dev/null -X POST http://127.0.0.1:8000/analytics/triggers/role_benchmark \
  -H "Content-Type: application/json" \
  -d '{"canonical_role_id":"bad;role","correlation_id":"audit-fail-1"}'
```

The route handler translates `ValueError` to `HTTPException 400`. Currently audit rows are written from the happy-path handler; failure-path auditing for triggers is tracked in the Pair D follow-up list. If no row appears for this correlation_id, flag it in the demo debrief, not a blocker.

---

## 9. Streamlit Dashboard — New Pages

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
2. **Sparse:** "Median salary for welders in El Paso in Q1 2025" → `volume_flagged_low=true` or refusal.
3. **Off-topic:** "What's the weather in El Paso?" → intent `other` → cautious refusal or deflection.

### Skills Gap Map (Pair D)

Consumes `dbo.cohort_gap_cache`. If the table is empty, the page should show an informative "no data" state, not a crash.

**Verification:**
- Trigger the cache at least once: `curl -X POST http://127.0.0.1:8000/analytics/triggers/cohort_gap_analysis -H "Content-Type: application/json" -d '{"cohort_key":"demo-cohort"}'`.
- Refresh the page; the cached cohort row's `gap_data.market_skill_demand` should render as a bar/heat chart.

### Emergence Alerts (Pair C, data from Pair A)

Consumes `dbo.disruption_fingerprints` joined to `dbo.canonical_roles`. If either is empty, the page shows a "no data" state.

**Verification:**
- Refresh fingerprints (§4 Step 2) first.
- Page should show per-role rows with `disruption_category` badges (Displacement / Augmentation / Transformation / Emergence), `ai_intensity_trend` arrows, and `workflow_restructuring_score` as a gauge.

### Regional Heatmap (Pair D)

Consumes `dbo.geo_demand_weekly`. Requires at least 10+ rows with `region`/`subregion`/`posting_count`.

**Verification:**
- Select a week from the dropdown.
- Map / heat grid renders with real subregion names (`el_paso_metro`, `las_cruces`, `southern_nm`, `other`) and non-zero counts.

---

## 10. Cost Tracking

Every Q&A run populates `SynthesisResponse.cost_breakdown_usd` with per-leg spend. These numbers come from the LLM adapter (real token counts × `PRICING` dict in `common/llm_adapter.py`), not estimates.

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

```bash
PYTHONPATH=. python -c "
from common.llm_adapter import resolve_model_tier, PRICING
for dep in ('chat-gpt41mini', 'chat-gpt41', 'chat-gpt4o-mini', 'claude-sonnet-4-5'):
    tier = resolve_model_tier(dep)
    pricing = PRICING.get(tier, {})
    print(f'{dep:25} -> {tier:20} input=${pricing.get(\"input\", \"?\")}/1M  output=${pricing.get(\"output\", \"?\")}/1M')
"
```

**Expected:** `chat-gpt41mini → gpt-4.1-mini`, `chat-gpt41 → gpt-4.1`, each with populated input/output pricing per million tokens.

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

The page joins `disruption_fingerprints` to `canonical_roles` on `canonical_role_id`. If the fingerprint rows have `canonical_role_id` values that do not appear in `canonical_roles`, the inner join drops them. Confirm with:

```bash
python scripts/db_check.py query "SELECT COUNT(*) AS fp_rows FROM dbo.disruption_fingerprints"
python scripts/db_check.py query "SELECT COUNT(*) AS joined FROM dbo.disruption_fingerprints df JOIN dbo.canonical_roles cr ON cr.id = df.canonical_role_id"
```

If `fp_rows > joined`, the role ids are out of sync — fix in Pair A repository or re-seed `canonical_roles`.

**Regional Heatmap map tile missing**

The page uses Streamlit's built-in map / Plotly render from `dbo.geo_demand_weekly`. If the map is blank but the table shows rows, the subregion values may not match the expected lookup (`el_paso_metro`, `las_cruces`, `southern_nm`, `other`). Run:

```bash
python scripts/db_check.py query "SELECT DISTINCT subregion FROM dbo.geo_demand_weekly"
```

Unexpected subregion values indicate a Pair A Borderplex classification drift — see Week 7 runbook §5 Table 5.

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
