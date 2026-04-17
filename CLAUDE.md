# CLAUDE.md — Job Intelligence Engine

This file is the primary context document for Claude Code.
Read this before touching any file in the repo.
Source of truth: `job_intelligence_engine_architecture.docx` — see `docs/planning/ARCHITECTURE_DEEP.md` for full implementation spec.

---

## Project Summary

**Job Intelligence Engine** — an eight-agent (Phase 1) / nine-agent (Phase 2) Python pipeline that ingests, normalizes, enriches, and analyzes external job postings for the WFD OS platform. **SQLAlchemy is the single database authority.** All database tables are agent-managed via SQLAlchemy.

This repo was extracted from `job-intelligence-engine/` into a standalone repository. The Next.js frontend remains in the watechcoalition repo (legacy, being replaced by wfd-os). See also: `Building-With-Agents/wfd-os` (Phase 2 platform).

## Related Repos

| Repo | Purpose |
|------|---------|
| **watechcoalition** | Legacy Next.js frontend (being replaced by wfd-os) |
| **wfd-os** | Phase 2 platform: student portal, WFD OS agents, Waifinder BD Pipeline |
| **curriculum-planning** | Instructor-facing curriculum planning |
| **curriculum** | Student-facing materials |
| **bwa-discordbot** | Code review bot |

---

## Non-Negotiable Rules

1. **One agent = one responsibility.** Helper logic (dedup, validation, field mapping, confidence scoring) stays encapsulated inside the owning agent. Never promoted to its own agent.
2. **Agents communicate via typed, versioned events only.** Direct function calls between agents are forbidden.
3. **The Orchestration Agent is the sole consumer** of `*Failed` and `*Alert` events. No other agent reacts to another agent’s failures.
4. **No agent writes to another agent’s internal state.**
5. **Every agent exposes a `health_check()` method** and emits self-evaluation metrics.
6. **SQLAlchemy is the single database authority.** All schema changes go through `common/data_store/models.py` and `migrations.py`.
7. **No credentials in code or logs.** Environment variables only.
8. **Do NOT implement Phase 2 items during Phase 1** unless explicitly instructed.
9. **All agent pipeline tables are permanent — never delete or truncate any of them.** Every table (`raw_ingested_jobs`, `job_ingestion_runs`, `normalized_jobs`, `normalization_quarantine`, `extracted_intelligence`, `llm_audit_log`) is retained for record keeping and model evaluation. `raw_ingested_jobs` is also the dedup fingerprint store — clearing it causes duplicate re-ingestion. `clean_stale_postings.py` is deprecated (no-op).
   - **Export before any destructive operation.** Before running `docker compose down -v`, dropping tables, or any command that destroys data, always export current fixtures first: `python scripts/pg-seed-data/export_fixtures.py --scope all`. This ensures the latest pipeline output is committed and recoverable.
   - **`docker compose down -v` is for dev-local Docker environments only.** Never run it against the admin source-of-truth database (Gary's local or Azure). The admin database is the origin for all fixtures — wiping it loses unrecoverable pipeline data.
   - **Test fixtures must never TRUNCATE or DELETE these tables** — use a unique test-run ID to tag inserted rows and delete only those rows in teardown.

---

## LLM Model References — Azure OpenAI Is the Default Provider

All LLM calls route through the provider-agnostic adapter
(`common.llm_adapter.complete`) with role-based resolution. Do not hardcode
Anthropic model IDs (`claude-haiku-4-5`, `claude-sonnet-4-6`,
`claude-opus-4-6`, etc.) in agent code, `.cursor/rules/*.mdc` contracts,
runbooks, or documentation.

**Deployment map:**

| Tier | Azure deployment | Env var |
|---|---|---|
| Haiku-class | `chat-gpt41mini` | `LLM_DEFAULT` |
| Sonnet-class | `chat-gpt41` | `LLM_SYNTHESIS` |
| Embeddings | `embeddings-te3small` | `AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME` |

See `.cursor/rules/llm-provider.mdc` (repo-wide, `alwaysApply: true`) for the
full rule and `.cursor/rules/llm-routing.mdc` for the role-based resolution
spec. The `anthropic` provider is kept as a fallback in `common.llm_adapter`
but is not the default for any agent.

---

## Table Ownership

**SQLAlchemy is the single database authority.** All tables live in the `dbo` schema on PostgreSQL.

| Category | Tables | Notes |
|----------|--------|-------|
| **Agent-created** | `raw_ingested_jobs`, `job_ingestion_runs`, `normalized_jobs`, `normalization_quarantine`, `extracted_intelligence`, `llm_audit_log`, `employer_profiles` | Created by `migrations.py` |
| **Reference (seeded, agent-owned)** | `companies`, `industry_sectors`, `technology_areas`, `skills`, `socc`, `job_postings` | Seeded via `seed_pg_database.py`; agents have full read+write |

**Rules:**
- New tables and columns go through `common/data_store/models.py` + `migrations.py`
- Enrichment output (quality_score, is_spam, soc_code, etc.) goes to `job_postings` columns
- Company resolution writes placeholders directly to the `companies` table

---

## Repository Structure

```
/                              ← JIE repo root
├── ingestion/
│   ├── agent.py
│   ├── sources/
│   ├── deduplicator.py
│   └── tests/
├── normalization/
│   ├── agent.py
│   ├── schema/
│   ├── field_mappers/
│   └── tests/
├── skills_extraction/
│   ├── agent.py
│   ├── models/
│   ├── taxonomy/
│   └── tests/
├── enrichment/
│   ├── agent.py
│   ├── classifiers/
│   ├── resolvers/
│   └── tests/
├── analytics/
│   ├── agent.py
│   ├── aggregators/
│   ├── clustering/
│   ├── canonical_roles/
│   ├── insights/
│   ├── query_engine/
│   └── tests/
├── visualization/
│   ├── agent.py
│   ├── renderers/
│   ├── exporters/
│   └── tests/
├── orchestration/
│   ├── agent.py
│   ├── scheduler/
│   ├── circuit_breaker/    ← Phase 2 scaffold
│   ├── saga/               ← Phase 2 scaffold
│   ├── admin_api/          ← Phase 2 scaffold
│   └── tests/
├── demand_analysis/        ← Phase 2: reserved for Query Agent
├── common/
│   ├── events/
│   ├── message_bus/
│   ├── data_store/
│   ├── observability/
│   ├── types/
│   ├── config/
│   ├── errors/
│   ├── llm_adapter.py
│   └── llm_client.py
├── dashboard/              ← Streamlit analytics UI
├── scripts/                ← batch_ingest.py, run_processing_loop.py, seed scripts
├── data/                   ← staging/, normalized/, enriched/, dead_letter/
├── eval/                   ← ground truth, cost audits
├── tests/                  ← integration tests
├── docs/                   ← ADRs, planning, findings, runbooks
├── .cursor/rules/          ← per-workstream Cursor rule files
├── CLAUDE.md
├── README.md
├── .env.example
├── pyproject.toml
├── requirements.txt
├── conftest.py
├── pipeline_runner.py
└── docker-compose.yml
```

---

## Architecture — Eight Agents (Phase 1), Nine Agents (Phase 2)

```
Sources (JSearch API via httpx / Web scraping via Crawl4AI)
    ↓  [daily cron: INGESTION_SCHEDULE]
[Ingestion Agent]         → IngestBatch
    ↓
[Normalization Agent]     → NormalizationComplete
    ↓
[Skills Extraction Agent] → SkillsExtracted
    ↓
[Enrichment Agent]        → RecordEnriched
    ↓
[Analytics Agent]         → AnalyticsRefreshed
    ↓                        (Phase 2: absorbs demand analysis capabilities)
[Visualization Agent]     → RenderComplete

[Orchestration Agent]     ← sole consumer of ALL *Failed/*Alert events
                          ← schedules, monitors, retries all agents above

[Query Agent]             ← Phase 2 (9th agent) — standalone workforce Q&A
                          ← Gap Analysis, Candidate-Role Match, Stakeholder Reports
```

---

## Tech Stack (IC = infrastructure constraint; SA = student ADR with reference implementation)

| Layer | Technology | Decision |
|-------|-----------|---------|
| Agent runtime | Python 3.11 (pinned) | — |
| Multi-agent framework | LangGraph StateGraph | SA #13 |
| LLM adapter | LangChain + Azure OpenAI (provider-agnostic) | SA #11 |
| LLM provider default | Azure OpenAI; switchable via `LLM_PROVIDER` env var | SA #11 |
| Agent tracing | Langfuse — open-source, self-hosted or cloud (ADR-006) | SA #17 |
| Scheduling | APScheduler — inside Orchestration Agent | IC #3 |
| Ingestion: API source | httpx — JSearch API calls | SA #12 |
| Ingestion: web scraping | Crawl4AI — local, pip-installable | SA #12 |
| DB access (all) | SQLAlchemy + psycopg2 → PostgreSQL (single authority) | IC #19 |
| Message bus (Phase 1) | In-process Python pub/sub | SA #14 |
| Message bus (Phase 2) | External bus (Kafka / RabbitMQ / Redis Streams) | SA #14 |
| Dashboards | Streamlit — read-only SQLAlchemy connection | — |
| Analytics query interface | REST — `POST /analytics/query` | SA #18 |
| Logging | structlog — JSON-formatted, no PII | — |
| Testing | pytest | — |

SA-classified decisions use the **reference implementation** shown above. If your team's Week 2 ADRs select different technologies, adapt the implementation while preserving the agent contracts. All SA decisions must converge before the team implements the agent that depends on it.

---

## Environment Variables

All credentials in `.env` — never hardcode any of these.

```bash
# LLM Routing (see .env.example for full list)
LLM_PROVIDER=azure_openai          # azure_openai | gemini | anthropic | mock
LLM_DEFAULT=chat-gpt41mini         # Haiku-class — default for all calls
LLM_SYNTHESIS=chat-gpt41           # Sonnet-class — Q&A synthesis, complex reasoning
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=

# Database
PYTHON_DATABASE_URL=                # SQLAlchemy psycopg2 URL:
                                    #   postgresql+psycopg2://user:pass@host:port/db

# Tracing
LANGSMITH_API_KEY=
LANGCHAIN_TRACING_V2=true

# Ingestion
JSEARCH_API_KEY=
SCRAPING_TARGETS=                   # Comma-separated target URLs
INGESTION_SCHEDULE=0 2 * * *        # Cron expression — default: daily at 2am

# Pipeline thresholds
SPAM_FLAG_THRESHOLD=0.7             # Flag for operator review above this
SPAM_REJECT_THRESHOLD=0.9           # Auto-reject above this
SKILL_CONFIDENCE_THRESHOLD=0.75
SKILLS_EXTRACTION_PARALLEL=1        # Run tasks/responsibilities/skills concurrently and process jobs in parallel
SKILLS_EXTRACTION_CONCURRENCY=5     # Max jobs in flight when parallel mode is enabled
# Deprecated serial-only throttles; used only when SKILLS_EXTRACTION_PARALLEL=0
SKILLS_EXTRACTION_CHUNK_SIZE=5
SKILLS_EXTRACTION_CHUNK_COOLDOWN=30
SKILLS_EXTRACTION_DELAY=1.0
BATCH_SIZE=100

# Enrichment (proven stable config — do NOT increase concurrency above 5)
ENRICHMENT_PARALLEL=1               # 1 = async parallel, 0 = serial fallback
ENRICHMENT_CONCURRENCY=5            # MUST be ≤ 5 — higher values cause async hangs from DB pool exhaustion
ENRICHMENT_LLM_TIMEOUT=120          # Seconds — gather timeout for SOC+NAICS+employer per job
```

---

## Common Patterns — Follow These Exactly

### Event envelope (every inter-agent event must use this shape)

```python
# common/event_envelope.py
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Any
import uuid

class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str    # propagated unchanged from IngestBatch through all downstream events
    agent_id: str          # e.g. "ingestion-agent"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    schema_version: str = "1.0"  # increment on breaking payload changes
    payload: dict[str, Any]
```

### LLM routing — per-call model selection

All LLM calls use `resolve_llm_route(role)` to determine which provider and deployment to use. Configuration is via `LLM_*` env vars.

```python
from common.llm_adapter import complete, resolve_llm_route

# Role-based (preferred — resolves via LLM_SYNTHESIS env var):
result = complete(prompt=prompt, agent_name="analytics-agent", role="synthesis")

# Direct resolution:
provider, deployment = resolve_llm_route("synthesis")  # ("azure_openai", "chat-gpt41")
```

**Env var resolution:** `LLM_{ROLE}` → `LLM_DEFAULT` → `ValueError`. No legacy fallbacks.

**Provider override:** `LLM_SYNTHESIS=gemini:gemini-2.5-pro` routes synthesis through Gemini.

**Roles:** `synthesis`, `extraction`, `extraction_tasks`, `extraction_responsibilities`, `extraction_naics`, `extraction_employer`, `classification`, `analytics`

**Never hardcode deployment names.** Always use `role=` parameter or `resolve_llm_route()`.

Fallback: 2 retries → log to `llm_audit_log` → set `extraction_status = "failed"` → continue batch. Never block a batch on LLM failure.

### Structured logging (no PII ever)

```python
import structlog
log = structlog.get_logger()
log.info("ingestion_batch_complete", batch_id=batch_id, record_count=n, dedup_count=d)
```

### Health check (required on every agent class)

```python
def health_check(self) -> dict:
    return {
        "status": "ok",       # "ok" | "degraded" | "down"
        "agent": "ingestion",
        "last_run": self.last_run_at.isoformat(),
        "metrics": self.last_run_metrics
    }
```

---

## Database Schema

**Key existing tables (all managed via SQLAlchemy):**
- `job_postings` — canonical job records; final destination for enriched jobs
- `companies`, `company_addresses` — company reference data
- `skills` — canonical skill taxonomy with embeddings; M:M with job_postings
- `technology_areas`, `industry_sectors` — classification taxonomy

**New columns on `job_postings` (SQLAlchemy migration):**

```sql
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS external_id TEXT;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS ingestion_run_id TEXT;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS ai_relevance_score DOUBLE PRECISION;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS is_spam BOOLEAN;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS spam_score DOUBLE PRECISION;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS overall_confidence DOUBLE PRECISION;
ALTER TABLE job_postings ADD COLUMN IF NOT EXISTS field_confidence JSONB;
```

**Agent-managed tables (created via SQLAlchemy migrations):**

| Table | Phase | Purpose |
|-------|-------|---------|
| `raw_ingested_jobs` | 1 | Ingestion staging |
| `normalized_jobs` | 1 | Post-normalization records |
| `job_ingestion_runs` | 1 | Batch run tracking |
| `alerts` | 1 | Active and historical alerts |
| `orchestration_audit_log` | 1 | All orchestration decisions (100% completeness required) |
| `llm_audit_log` | 1 | All LLM calls (prompt hash, model, latency, tokens) |
| `analytics_aggregates` | 1 | Computed aggregate tables |
| `demand_signals` | 2 | Trend and forecast outputs |

**Important:** Write to `job_postings` only after `company_id` is resolved. Do not write a record with a null `company_id`.

---

## Per-Agent Quick Reference

| Agent | File | Phase | Emits | Consumes |
|-------|------|-------|-------|---------|
| Ingestion | `ingestion/agent.py` | 1 | `IngestBatch` | — |
| Normalization | `normalization/agent.py` | 1 | `NormalizationComplete` | `IngestBatch` |
| Skills Extraction | `skills_extraction/agent.py` | 1 | `SkillsExtracted` | `NormalizationComplete` |
| Enrichment (lite) | `enrichment/agent.py` | 1 | `RecordEnriched` | `SkillsExtracted` |
| Analytics | `analytics/agent.py` | 1 | `AnalyticsRefreshed` | `RecordEnriched` |
| Visualization | `visualization/agent.py` | 1 | `RenderComplete` | `AnalyticsRefreshed` |
| Orchestration | `orchestration/agent.py` | 1 | trigger/retry signals | ALL events incl. `*Failed`/`*Alert` |
| Query Agent | `query/agent.py` | 2 | `QueryResponse` | `AnalyticsRefreshed` |

---

## Key Agent Behaviors

### Ingestion Agent
- Sources: JSearch via `httpx`; web scraping via Crawl4AI
- **JSearch queries must be broad across sites** (no `site` filter parameter). Narrowing is done via `RegionConfig` (location, keywords, role_categories) — not by restricting to specific job boards. Site-filtered queries return incomplete data (missing `job_description`).
- Fingerprint: `sha256(source + external_id + title + company + date_posted)`
- **JSearch wins over scraped** when the same job appears in both sources (IC #9)
- Dedup before staging — duplicates discarded silently, counter incremented
- Provenance tags on every record: `source`, `external_id`, `raw_payload_hash`, `ingestion_run_id`, `ingestion_timestamp`

### Normalization Agent
- Maps source fields → canonical `JobRecord` via per-source field mappers
- Standardizes: dates (ISO 8601), salaries (min/max/currency/period), locations, employment types
- Quarantines schema violations — never passes bad records downstream

### Skills Extraction Agent *(Work Intelligence Agent)*
- **No fixture fallback in testing or production.** When normalized text is empty or extraction fails, the agent must fail explicitly (`extraction_status = "failed"`, `extraction_failed = True`) with a clear `error_reason` — never silently return fixture/placeholder data. Fixture data masks real pipeline issues (e.g., empty descriptions from JSearch, misconfigured normalization mappers). Fixture fallback is only acceptable in walking-skeleton demos (Week 2).
- Parallel execution: when `SKILLS_EXTRACTION_PARALLEL=1`, the agent runs tasks, responsibilities, and skills concurrently inside each job and processes multiple jobs concurrently up to `SKILLS_EXTRACTION_CONCURRENCY`. If the caller already has a running event loop, the agent falls back to the serial path to preserve the synchronous `process()` contract.
- Deprecated serial throttles: `SKILLS_EXTRACTION_CHUNK_SIZE`, `SKILLS_EXTRACTION_CHUNK_COOLDOWN`, and `SKILLS_EXTRACTION_DELAY` apply only when `SKILLS_EXTRACTION_PARALLEL=0`.
- Taxonomy linking order (strict):
  1. Exact match → GenAI Extension Layer (10 predefined GenAI skills; `is_genai_extension = True`, `esco_uri` maps to parent ESCO cluster)
  2. Exact name match → ESCO digital skills cluster (maps to `skills` table)
  3. Normalized name match → ESCO digital skills cluster (maps to `skills` table)
  4. Embedding cosine similarity ≥ 0.92 → ESCO digital skills cluster
  5. O\*NET occupation code match
  6. Emit as `raw_skill` (null taxonomy_id) — Enrichment resolves in Phase 2
- **ESCO embeddings in PostgreSQL:** Step 4 cosine similarity requires pre-computed embeddings for all skills in `dbo.skills`. These are stored in the `embedding vector(1536)` column (pgvector). **Admin seeds embeddings once** via `python scripts/seed_esco_embeddings.py` against Azure PostgreSQL. Devs pull embeddings from the Azure DB to their local PostgreSQL. **Never call Azure OpenAI embedding API for the full corpus** — only the admin seeding script does this. The taxonomy resolver loads embeddings from PostgreSQL on first use and caches in memory for the process lifetime.
- Log every LLM call to `llm_audit_log`

### Enrichment Agent (Phase 1 lite)
- Classify role and seniority
- Quality score [0–1]: completeness + clarity + AI keyword density + structural coherence
- Spam detection: `spam_score` < 0.7 → proceed | 0.7–0.9 → flag for review (`is_spam = null`) | > 0.9 → auto-reject, do NOT write to `job_postings`
- Resolve `company_id` before writing: match `companies` by name → no match → create placeholder
- Map `sector_id` → `industry_sectors`

### Analytics Agent
- Aggregates across 6 dimensions: skill, role, industry, region, experience level, company size
- Salary distributions: median, p25, p75, p95 per dimension
- Co-occurrence matrices, posting lifecycle metrics (time-to-fill proxies, repost rates)
- Weekly summaries: LLM-generated; deterministic template fallback if LLM unavailable
- SQL guardrails (always enforced): SELECT only | allowed tables only | no DDL/DML | 100-row limit | 30s timeout
- Cardinality explosion: cap dimensions; coalesce long-tail into “Other”; emit warning

### Visualization Agent
- Dashboard pages: Ingestion Overview | Normalization Quality | Skill Taxonomy Coverage | Weekly Insights | Ask the Data | Operations & Alerts
- Exports: PDF, CSV, JSON — **all standard Phase 1 deliverables, not stretch goals**
- TTL cache with staleness banner — never a blank page
- DB connection is **read-only**

### Orchestration Agent (Phase 1)
- Framework: LangGraph StateGraph [SA #13/#16] + APScheduler [IC]
- Sole consumer of all `*Failed` / `*Alert` events
- Alerting tiers:
  - **Warning:** logged + metric emitted
  - **Critical:** paged to on-call
  - **Fatal:** circuit broken + human escalation required
- Retry policies:

| Agent | Max retries | Back-off |
|-------|------------|---------|
| Ingestion (source unreachable) | 5 | Exponential + jitter |
| Normalization (batch failure) | 3 | Exponential |
| Skills Extraction (LLM timeout) | 2 per record | Fixed 2s |
| Any agent (transient DB error) | 3 | Exponential |

- Audit log: 100% completeness required — every trigger, retry, and alert creation must be recorded

### Query Agent (Phase 2 — 9th agent)
- Standalone workforce intelligence Q&A agent with persona-based response routing
- Three new output modes beyond Phase 1 Q&A: Gap Analysis, Candidate-Role Match, Stakeholder Reports
- `QueryPersona` enum: `workforce_board_director | employer_partner | cfa_internal | cohort_student`
- `QueryRequest` model: `query`, `persona`, `intent_hint` (optional), `max_results`
- Requires upstream data not in Phase 1: candidate/cohort profiles (new ingestion source)
- Forward-compatibility: `persona` field added to query API schema in Week 6 (Phase 1 ignores it; Phase 2 uses it for routing)
- Phase 1 Q&A (inside Analytics Agent) handles: intent classification, evidence citation, Comparative Analysis, Trend Narrative
- Phase 2 Query Agent handles: Gap Analysis, Candidate-Role Match, Stakeholder Reports, persona routing, formatted document generation (PDF/DOCX)

### Analytics Agent — Phase 2 Expansion
- Absorbs Demand Analysis Agent capabilities: time-series indexing, velocity windows (7d/30d/90d), 30-day demand forecasts, anomaly detection with significance testing, TrajectoryRecord projections
- Emits `DemandSignalsUpdated` and `DemandAnomaly` events (previously attributed to standalone Demand Analysis Agent)
- Q&A migrates out to standalone Query Agent in Phase 2; Analytics retains aggregation + disruption + demand analysis

---

## Evaluation Targets (non-negotiable — check against these)

| Agent | Metric | Target |
|-------|--------|--------|
| Ingestion | Success rate | ≥ 98% per 24h |
| Ingestion | Duplicate rate forwarded | < 0.5% |
| Ingestion | Dead-letter volume | < 1%; alert above 2% |
| Normalization | Schema conformance | ≥ 99% |
| Normalization | Field mapping accuracy | ≥ 97% (spot check) |
| Normalization | Quarantine rate | < 1%; alert above 3% |
| Skills Extraction | Taxonomy coverage | ≥ 95% |
| Skills Extraction | Precision at taxonomy link | ≥ 92% (human eval) |
| Skills Extraction | Recall of key skills | ≥ 88% |
| Skills Extraction | Avg confidence score | ≥ 0.80 |
| Analytics | Aggregate accuracy | ≥ 99.5% vs raw recount |
| Analytics | Query p50 latency | < 500ms |
| Analytics | Aggregate freshness | Within 15 min of new enriched batch |
| Visualization | Render success rate | ≥ 99.5% |
| Visualization | Dashboard freshness | Within 5 min of trigger |
| Visualization | Export generation p95 | < 10s |
| Visualization | Cache hit rate | ≥ 70% |
| Orchestration | Pipeline end-to-end SLA | ≥ 95% of batches |
| Orchestration | Mean time to detect failure | < 60s |
| Orchestration | Mean time to recover (auto) | < 5 min |
| Orchestration | Audit log completeness | 100% |
| System | Batch throughput | 1,000 jobs < 5 minutes |
| Query Agent (Phase 2) | Intent classification accuracy | ≥ 90% |
| Query Agent (Phase 2) | Response relevance (human eval) | ≥ 85% |
| Query Agent (Phase 2) | Persona routing accuracy | ≥ 95% |
| Analytics (Phase 2) | Forecast MAPE (30-day) | < 15% |
| Analytics (Phase 2) | Trend accuracy | ≥ 85% |
| Analytics (Phase 2) | Anomaly precision | ≥ 80% |

---

## Security Rules (enforced always)

- No credentials in code or logs — environment variables only
- No PII in log output (no job seeker names, emails, or personal data)
- Analytics Agent SQL: SELECT only | allowed tables only | no DDL/DML | 100-row limit | 30s timeout
- Visualization Agent DB connection: read-only
- All SQL validation failures logged to `llm_audit_log`

---

## Build Order (12-Week Curriculum)

| Week | Deliverable | Key outputs |
|------|------------|-------------|
| 1 | Environment + first scrape + basic Streamlit | Working Python env, raw JSON scrape, Streamlit prototype |
| 2 | LLM adapter + walking skeleton | `llm_adapter.py`, 8 agent stubs, pipeline runner, journey dashboard |
| 3 | Ingestion Agent + Normalization Agent | `IngestBatch`/`NormalizationComplete` events, staging tables, APScheduler |
| 4 | Work Intelligence Agent Part 1 | Skills + Tools + Taxonomy + Cost Modeling, `SkillsExtracted` event, eval dataset (30–50 labeled) |
| 5 | Work Intelligence Agent Part 2 + Enrichment-lite | Tasks + Responsibilities + Context extraction, prompt iteration log |
| 6 | Enrichment Agent (Full Phase 1) + Visualization Foundations | Temporal/borderplex classification, fuzzy dedup, quality/spam scoring, Streamlit dashboards |
| 7 | Analytics Agent — aggregates + weekly insights | Aggregate tables, LLM summaries, template fallback, `AnalyticsRefreshed` event |
| 8 | Analytics Agent — Ask the Data | Text-to-SQL, SQL guardrails + unit tests, “Ask the Data” Streamlit page |
| 9 | Orchestration Agent + Visualization Completion | Scheduling, alerting tiers, retry policies, audit log, Operations & Alerts page, PDF/CSV/JSON export |
| 10 | Pipeline hardening + event contract enforcement | Near-dedup in Ingestion, enrichment tuning, perf benchmark, 1k-job load test |
| 11 | Testing + security + documentation | Integration test suite, security checklist, Dockerfile, ARCHITECTURE.md, RUNBOOK.md |
| 12 | Capstone demo + release | Live demo to stakeholders, `v0.1.0-capstone` tag, handoff package, retrospective |
| Phase 2 | Query Agent (9th agent) | Standalone Q&A: Gap Analysis, Candidate-Role Match, Stakeholder Reports, persona routing |

---

## Design Decisions (IC/SA/D Classification)

See `docs/planning/ARCHITECTURAL_DECISIONS.md` for full classification details and evaluation criteria.

**IC = Infrastructure Constraint (fixed).** **SA = Student ADR (reference implementation shown; open for team decision via ADR).** **D = Deferred to Phase 2.**

| # | Decision | Classification | Resolution |
|---|----------|----------------|------------|
| 3 | Batch vs real-time | IC | **Batch-first** — APScheduler, daily cron default (`0 2 * * *`) |
| 4 | Source of truth for ingested jobs | IC | **Staging tables + promotion** — `raw_ingested_jobs` → `normalized_jobs` → `job_postings` |
| 8 | Spam threshold | IC | **Tiered** — flag at 0.7, auto-reject above 0.9 |
| 9 | Dedup source priority | IC | **JSearch wins over scraped** on duplicate |
| 11 | LLM provider policy | SA | **Provider-agnostic adapter** — Azure OpenAI default, switchable via `LLM_PROVIDER` |
| 12 | Scraping tool | SA | **Crawl4AI** + **httpx** for JSearch API |
| 13 | Multi-agent framework | SA | **LangGraph** StateGraph |
| 14 | Message bus | SA | **In-process Python events** (Phase 1); external bus upgrade path for Phase 2 |
| 15 | Skill taxonomy | IC | **Internal watechcoalition primary** (`skills` table); O\*NET fallback |
| 16 | Orchestration engine | SA | **LangGraph StateGraph** (consistent with #13) |
| 17 | Agent tracing | SA | **Langfuse** — open-source, self-hosted or cloud (ADR-006) |
| 18 | Analytics query interface | SA | **REST** — `POST /analytics/query` |
| 19 | Database engine | IC | **PostgreSQL** — single instance, pgvector-enabled; see `ARCHITECTURAL_DECISIONS.md` #19 |
| 20 | Enrichment phase split | IC | **Lite (Phase 1) + Full (Phase 2)** |
| 21 | PDF export scope | IC | **Standard Phase 1 deliverable** — not a stretch goal |

### Still Open

| # | Decision | Recommendation |
|---|----------|----------------|
| 22 | Multi-tenancy | Single shared pipeline for Phase 1; revisit before any Phase 2 multi-org work |
| 23 | Feedback loop agent | Defer to Phase 2; requires ground truth source, training pipeline, and model versioning strategy |

---

## How to Run

All commands run from the **repo root** with the venv activated.

```bash
# One-time setup: create venv and install deps
py -3.11 -m venv .venv                      # Windows
.venv\Scripts\Activate.ps1                   # Windows PowerShell
pip install -r requirements.txt

# --- Flywheel pipeline (production) — decoupled ingestion + processing ---
# Loop 1: Bulk ingest from JSearch (budget-aware, key rotation)
python scripts/batch_ingest.py              # run all queries from config
python scripts/batch_ingest.py --dry-run    # show plan without API calls

# Loop 2: Paced processing (normalize → extract → enrich)
python scripts/run_processing_loop.py --batch-size 50 --delay 2
python scripts/run_processing_loop.py --dry-run        # show pending counts

# Seed local DB with reference + enriched data (idempotent, safe to re-run)
python scripts/pg-seed-data/seed_pg_database.py

# Run the Streamlit dashboard
streamlit run dashboard/streamlit_app.py

# Run agent tests
python -m pytest tests/ -v

# Run a single agent manually
# python -m ingestion.agent --source jsearch --limit 50
# python -m ingestion.agent --source crawl4ai --limit 50

# Demo run: Walking skeleton single-pass with fixture data (Week 2 demo only)
# python pipeline_runner.py
```

---

## What NOT to Do

- Do NOT create new agents for helper logic (dedup, validation, field mapping, etc.) — keep inside the owning agent
- Do NOT make agents call each other directly — use events
- Do NOT write to `job_postings` without a resolved `company_id`
- Do NOT store credentials in code or logs
- Do NOT implement Phase 2 items (circuit-breaking, saga, admin API, demand analysis (now Analytics Phase 2), Query Agent, full enrichment, external bus)
- Do NOT skip writing tests alongside implementation
- Do NOT serve a blank dashboard page — always serve stale data with a staleness banner

---

## Full Specification Reference

For complete implementation specs, read in this order:

1. `docs/planning/ARCHITECTURE_DEEP.md` — canonical implementation reference (per-agent specs, JobRecord schema, event catalog, DB migrations, error-handling)
2. `docs/planning/ARCHITECTURE_COMPLETE.md` — full intended system definition (no Phase scope reductions)
3. `docs/planning/ARCHITECTURE_MID_LEVEL.md` — architecture reference for tech leads (diagrams, per-agent summaries, evaluation targets)
4. `docs/planning/ARCHITECTURE_HIGH_LEVEL.md` — overview for directors and stakeholders
5. `docs/planning/ARCHITECTURAL_DECISIONS.md` — IC/SA/D decision tracker with evaluation criteria
6. `docs/planning/TRD.md` — technical requirements, inter-agent contracts, NFRs
7. `docs/planning/BRD.md` — business scope, success criteria, design decisions
8. `docs/planning/PRD.md` — user stories, feature list, UX requirements
9. Week files in `docs/planning/curriculum/` — weekly deliverables and exercises
10. `docs/planning/QA_DATA_CONTRACT.md` — Q&A schema contract: authoritative column catalog, join paths, JSONB extraction patterns, and canonical query recipes for the Ask-the-Data SQL generator (#177)

**Visual diagrams:** `docs/planning/component-diagram.html` (full system) and `docs/planning/component-diagram-walking-skeleton.html` (Week 2 walking skeleton)
