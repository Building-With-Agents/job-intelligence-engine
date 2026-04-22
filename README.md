# Job Intelligence Engine

Eight-agent Python pipeline that ingests, normalizes, enriches, and analyzes external job postings for the WFD OS platform. See [CLAUDE.md](CLAUDE.md) for architecture, event contracts, and build order.

## Quick Start

**Requires Python 3.11** (pinned — do not use 3.12+).

Create and activate the venv:

**Windows (PowerShell):**
```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux / macOS:**
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Setup

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required variables: `PYTHON_DATABASE_URL`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT_NAME`. See [ONBOARDING.md](ONBOARDING.md) for the full variable table.

## Docker Services

```bash
docker compose up -d
```

Starts PostgreSQL (app database), Redis, and Langfuse (6 containers for observability).

## Seed Database

```bash
# Run migrations (creates agent-managed tables)
python scripts/db_check.py migrate

# Seed local DB with reference + enriched data
python scripts/pg-seed-data/seed_pg_database.py

# Verify
python scripts/db_check.py counts
python scripts/verify_local_db_health.py   # full local health pass (Q&A, schema drift, referential checks)
```

## Run the Pipeline

```bash
# Flywheel Loop 1: Bulk ingest from JSearch
python scripts/batch_ingest.py
python scripts/batch_ingest.py --dry-run    # preview without API calls

# Flywheel Loop 2: Process (normalize -> extract -> enrich)
python scripts/run_processing_loop.py --batch-size 50 --delay 2
python scripts/run_processing_loop.py --dry-run    # show pending counts

# Demo run (fixture data, Week 2 walking skeleton)
# python pipeline_runner.py
```

## Streamlit Dashboard

```bash
streamlit run dashboard/streamlit_app.py
```

Opens at http://localhost:8501. Pages: Ingestion Overview, Normalization Quality, Skill Taxonomy Coverage, Weekly Insights, Ask the Data, Operations & Alerts.

## Azure PostgreSQL

See [Azure PostgreSQL Runbook](docs/runbooks/AZURE_POSTGRES_JOB_INTELLIGENCE_ENGINE.md) for provisioning and syncing.

## Tests

```bash
python -m pytest tests/ -v
```

## Agent Pipeline

```
Sources (JSearch API / Crawl4AI)
    |
[Ingestion]         -> IngestBatch
[Normalization]     -> NormalizationComplete
[Skills Extraction] -> SkillsExtracted
[Enrichment]        -> RecordEnriched
[Analytics]         -> AnalyticsRefreshed
[Visualization]     -> RenderComplete
[Orchestration]     <- monitors all agents, sole consumer of *Failed/*Alert events
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.11 |
| Multi-agent | LangGraph StateGraph |
| LLM | LangChain + Azure OpenAI (provider-agnostic) |
| Tracing | Langfuse |
| Scheduling | APScheduler |
| Ingestion | httpx (JSearch API) + Crawl4AI (scraping) |
| Database | PostgreSQL + pgvector via SQLAlchemy |
| Dashboards | Streamlit |
| Testing | pytest |

## Layout

```
ingestion/          — Ingestion Agent (JSearch, Crawl4AI sources)
normalization/      — Normalization Agent (field mapping, schema validation)
skills_extraction/  — Skills Extraction Agent (taxonomy linking, LLM extraction)
enrichment/         — Enrichment Agent (quality scoring, spam detection, company resolution)
analytics/          — Analytics Agent (aggregates, clustering, insights)
visualization/      — Visualization Agent (Streamlit renderers, PDF/CSV/JSON export)
orchestration/      — Orchestration Agent (scheduling, alerting, retry policies)
common/             — Events, message bus, LLM adapter, data store, config
dashboard/          — Streamlit app
scripts/            — Batch ingest, processing loop, seed scripts, verification
data/               — Staging, fixtures, output
eval/               — Ground truth, cost audits
tests/              — Integration tests
docs/               — ADRs, planning, findings, runbooks
```

## Related Repos

| Repo | Purpose |
|------|---------|
| [wfd-os](https://github.com/Building-With-Agents/wfd-os) | Phase 2 platform: student portal, WFD OS agents |
| [curriculum](https://github.com/Building-With-Agents/curriculum) | Student-facing materials |
| [curriculum-planning](https://github.com/Building-With-Agents/curriculum-planning) | Instructor-facing planning |

## License

Proprietary — Computing For All. All rights reserved.
