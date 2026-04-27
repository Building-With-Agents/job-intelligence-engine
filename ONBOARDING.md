# Onboarding — Job Intelligence Engine

Complete environment setup guide for the standalone Job Intelligence Engine repo.

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11 (pinned) | Do not use 3.12+ |
| Docker Desktop | Latest | For PostgreSQL, Redis, Langfuse |
| Git | Latest | |
| Azure OpenAI key | — | Or use `LLM_PROVIDER=mock` for local dev |

## 1. Clone and Install

```bash
git clone https://github.com/Building-With-Agents/job-intelligence-engine.git
cd job-intelligence-engine
```

Create the Python virtual environment:

**Windows (PowerShell):**
```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux / macOS:**
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Environment Configuration

Copy the example and fill in your credentials:

```bash
cp .env.example .env
```

### Required Variables

| Variable | Example | Purpose |
|----------|---------|---------|
| `PYTHON_DATABASE_URL` | `postgresql+psycopg2://user:pass@localhost:5432/talent_finder` | SQLAlchemy connection string |
| `LLM_PROVIDER` | `azure_openai` | `azure_openai`, `openai`, `anthropic`, or `mock` |
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | `https://<resource>.openai.azure.com/` | Azure OpenAI endpoint |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | `chat-gpt41mini` | Deployment name |
| `JSEARCH_API_KEY` | — | JSearch API key (for ingestion) |

### Langfuse (Observability)

| Variable | Value |
|----------|-------|
| `LANGFUSE_SECRET_KEY` | `sk-lf-local-dev-secret` |
| `LANGFUSE_PUBLIC_KEY` | `pk-lf-local-dev-public` |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` |
| `LANGFUSE_HOST` | `http://localhost:3001` |

### Mock Provider (no API keys needed)

Set `LLM_PROVIDER=mock` to run the full pipeline with simulated LLM responses. Mock calls produce real Langfuse traces with simulated token counts.

## 3. Start Docker Services

```bash
docker compose up -d
```

This starts:
- **PostgreSQL** (port 5432) — application database with pgvector
- **Redis** (port 6379) — inter-agent message bus
- **Langfuse** (port 3001) — 6 containers for observability

Verify all containers are healthy:

```bash
docker compose ps
```

### Langfuse Login

Open http://localhost:3001 and sign in:
- Email: `dev@localhost.dev`
- Password: `LocalDev123!`
- Organization: Computing For All
- Project: job-intelligence-engine

## 4. Database Setup

### Run Migrations

```bash
PYTHONPATH=. python scripts/db_check.py migrate
```

Creates all agent-managed tables (`raw_ingested_jobs`, `normalized_jobs`, `extracted_intelligence`, analytics aggregates, etc.).

### Seed Data

> **Requires Git LFS** — five seed fixtures are stored in Git LFS because they
> exceed GitHub's 50 MB recommendation. Install once per machine (Windows: bundled
> with Git for Windows, or `winget install GitHub.GitLFS`; macOS: `brew install
> git-lfs`; Linux: `apt install git-lfs`), run `git lfs install`, and `git lfs
> pull` if your clone is missing the real files. Without LFS the seeder loads
> empty arrays from pointer files. Details:
> [`scripts/pg-seed-data/README.md`](scripts/pg-seed-data/README.md#prerequisites-git-lfs).

The seed script handles reference data and enriched pipeline data in one pass:

```bash
python scripts/pg-seed-data/seed_pg_database.py
```

This seeds:
- Reference tables: `companies` (~554), `skills`, `naics` (~2,125), `industry_sectors`, `technology_areas`, `socc`
- Pipeline data: `raw_ingested_jobs` (~1,080), `job_postings` (~596+), `extracted_intelligence`, `llm_audit_log`

### Verify

```bash
PYTHONPATH=. python scripts/db_check.py tables   # list all dbo tables
PYTHONPATH=. python scripts/db_check.py counts   # row counts for agent tables
```

## 5. Run the Pipeline

### Flywheel Architecture (Production)

The pipeline runs as two decoupled loops:

```bash
# Loop 1: Bulk ingest from JSearch (budget-aware, key rotation)
python scripts/batch_ingest.py
python scripts/batch_ingest.py --dry-run    # preview without API calls

# Loop 2: Paced processing (normalize -> extract -> enrich)
python scripts/run_processing_loop.py --batch-size 50 --delay 2
python scripts/run_processing_loop.py --dry-run    # show pending counts
```

### Quick Re-test (3 jobs)

If the database is already fully processed:

```bash
# Roll back 3 jobs to pending
python scripts/reset_sample_jobs.py

# Re-process them
python scripts/run_processing_loop.py --max-iterations 1 --batch-size 3

# Verify
PYTHONPATH=. python scripts/db_check.py counts
```

## 6. Streamlit Dashboard

```bash
streamlit run dashboard/streamlit_app.py
```

Opens at http://localhost:8501. Dashboard pages:
- Ingestion Overview
- Normalization Quality
- Skill Taxonomy Coverage
- Weekly Insights
- Ask the Data
- Operations & Alerts

## 7. Run Tests

```bash
PYTHONPATH=. python -m pytest tests/ -v
```

## 8. Analytics Aggregates (Week 7+)

After pipeline data exists, populate analytics tables:

```bash
# List available weeks
PYTHONPATH=. python scripts/verify_aggregates.py --list-weeks

# Verify aggregates for a specific week
PYTHONPATH=. python scripts/verify_analytics_aggregates.py --week <YYYY-MM-DD>
```

## 9. Schema Changes

**SQLAlchemy is the single database authority.** All schema changes go through:
1. `common/data_store/models.py` — add/modify SQLAlchemy models
2. `common/data_store/migrations.py` — add migration step
3. Run: `PYTHONPATH=. python scripts/db_check.py migrate`

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError` | Ensure `PYTHONPATH=.` is set or run from repo root |
| `PYTHON_DATABASE_URL not set` | Check `.env` file exists at repo root |
| `connection refused` on port 5432 | Run `docker compose up -d` |
| Langfuse page won't load | Check `docker compose ps` — all 6 Langfuse containers must be `Up` |
| `LLM_PROVIDER` not working | Valid values: `azure_openai`, `openai`, `anthropic`, `mock` |
| Empty analytics tables | Run `process_aggregates()` or the Analytics Agent (Week 7) |
| `relation does not exist` | Run `PYTHONPATH=. python scripts/db_check.py migrate` |

## Further Documentation

- [CLAUDE.md](CLAUDE.md) — Architecture, agent contracts, build order
- [docs/planning/ARCHITECTURE_DEEP.md](docs/planning/ARCHITECTURE_DEEP.md) — Full implementation spec
- [docs/runbooks/](docs/runbooks/) — Weekly testing runbooks
- [docs/runbooks/AZURE_POSTGRES_JOB_INTELLIGENCE_ENGINE.md](docs/runbooks/AZURE_POSTGRES_JOB_INTELLIGENCE_ENGINE.md) — Azure DB setup
