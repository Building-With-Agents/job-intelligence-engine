# PostgreSQL Seed Data

Seed a fresh PostgreSQL container with reference data for the watechcoalition platform.

> **MSSQL is deprecated.** PostgreSQL is the primary database for all development.
> You do **not** need MSSQL installed or running.

## Quick Start (Junior Devs)

```bash
# 1. Start PostgreSQL container
docker compose --env-file .env.docker up postgres -d

# 2. Activate Python venv
.venv\Scripts\Activate.ps1          # Windows PowerShell
# source .venv/bin/activate         # macOS / Linux

# 3. Install dependencies (if not done yet)
pip install -r requirements.txt

# 4. Seed all data (reference + agent pipeline) — idempotent, safe to re-run
python scripts/pg-seed-data/seed_pg_database.py

# 5. Verify — run the flywheel pipeline
python scripts/batch_ingest.py --dry-run
python scripts/run_processing_loop.py --dry-run
```

**`seed_pg_database.py`** is **idempotent**: it uses `INSERT ... ON CONFLICT DO NOTHING` so existing records are never overwritten or deleted. New fixture records are added automatically. Seeds both reference data (`fixtures/*.json`) and agent pipeline data (`agent-fixtures/*.json`) in one command.

## What Gets Seeded

The seed script populates **40 reference tables** with ~56,000 rows:

| Table | Rows | Description |
|-------|------|-------------|
| `postal_geo_data` | 33,787 | WA ZIP codes with lat/long |
| `cip_to_socc_map` | 6,097 | CIP-to-SOC crosswalk |
| `skills` | 5,683 | Skill taxonomy (embeddings excluded) |
| `cip` | 2,849 | Classification of Instructional Programs |
| `_jobpostingskills` | 1,628 | Job-to-skill associations |
| `socc` | 1,024 | Standard Occupation Classification |
| `socc_2018` / `socc_2010` | 868 / 841 | SOC version variants |
| `jobroleskill` | 826 | Role-to-skill links |
| `programs` | 757 | Training programs |
| `provider_programs` | 745 | Provider-program associations |
| `jobroletraining` | 276 | Role-to-training links |
| `edu_providers` | 266 | Education providers |
| `job_postings` | 172 | Job listings |
| `pathwaytraining` | 162 | Pathway-training links |
| `company_addresses` | 149 | Company locations |
| `training` | 135 | Training records |
| `companies` | 122 | Company master data |
| `skill_subcategories` | 85 | Skill subcategory groupings |
| `jobrole` | 48 | Role definitions |
| `events` | 38 | Calendar events |
| + 20 more tables | 0–24 | Reference taxonomies & empty join tables |

### What is NOT seeded

- **PII tables** (users, jobseekers, employers, auth) — excluded for privacy
- **Agent pipeline rows** — `seed_pg_database.py` creates agent tables via `run_migrations()` and loads pipeline data from `agent-fixtures/*.json` automatically
- **Skill embeddings** — the `embedding` column is excluded from fixtures (107MB of pgvector data). Regenerate via the admin tool if needed.

## How It Works

### `seed_pg_database.py` (one command seeds everything)

Idempotent — safe to re-run at any time. Never deletes or overwrites existing data.

1. **Checks schema** — if dbo schema has no tables (fresh DB), runs `schema.sql` DDL; otherwise skips
2. **Runs agent migrations** — creates agent-managed tables + adds Phase 1 columns to `job_postings`
3. **Loads reference fixtures** — `INSERT ... ON CONFLICT DO NOTHING` from `fixtures/*.json` in FK-safe tier order
4. **Loads agent pipeline data** — calls `seed_agent_data.py` internally (same UPSERT pattern from `agent-fixtures/*.json`)

### `seed_agent_data.py` (called automatically, can also run standalone)

Loads `agent-fixtures/*.json` into staging/enrichment tables (and additional `job_postings` rows) via `INSERT … ON CONFLICT DO NOTHING`. Row counts are documented in `agent-fixtures/metadata.json`.

## File Structure

```
scripts/pg-seed-data/
  README.md                     ← This file
  seed_pg_database.py           ← Seed everything: reference + pipeline (junior devs run this)
  seed_agent_data.py            ← Pipeline data only (called by seed_pg_database.py, or standalone)
  export_fixtures.py            ← Export fixtures (admin only) — --scope reference|agent|all
  clean_stale_postings.py       ← DEPRECATED no-op (all data retained permanently)
  clean_schema.py               ← Schema cleaner (admin only)
  schema.sql                    ← Cleaned DDL (idempotent)
  schema_raw.sql                ← Raw pg_dump output (admin reference)
  fixtures/
    metadata.json               ← Export metadata with row counts
    skills.json                 ← 5,683 skills (no embeddings)
    companies.json              ← 122 companies
    job_postings.json           ← 172 job postings
    ... (40 fixture files)
  agent-fixtures/
    raw_ingested_jobs.json      ← Ingested job data
    normalized_jobs.json        ← Normalized records
    extracted_intelligence.json ← Extraction results
    job_postings.json           ← Enriched postings (promotion path; UPSERT after reference seed)
    ...                         ← job_ingestion_runs, normalization_quarantine, llm_audit_log, metadata.json
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ERROR: Set PYTHON_DATABASE_URL` | Add `PYTHON_DATABASE_URL=postgresql+psycopg2://postgres:YourPassword@localhost:5432/talent_finder` to your `.env` file |
| `Waiting for PostgreSQL...` loops | Ensure the postgres container is running: `docker compose --env-file .env.docker up postgres -d` |
| `ERROR executing schema DDL` | The database may have conflicting objects. Try: `docker compose down -v` then start fresh |
| FK constraint violations | This should not happen (triggers are disabled during load). If it does, file a bug. |
| Count mismatches after seeding | Re-run the seed script. If mismatches persist, re-export fixtures from the admin database. |
| `Could not import agent migrations` | Ensure `requirements.txt` is installed and you're in the venv. The seed still works — agent tables will be created when you first run the pipeline. |
| Embeddings are NULL in skills | Expected. Embeddings are excluded from fixtures (107MB). They are regenerated via the admin embedding tool. |

## For Admins: Re-exporting Fixtures

If the admin database changes, re-export fixtures:

```bash
# 1. Ensure PYTHON_DATABASE_URL points to the admin PostgreSQL instance
# 2. Export all fixtures (reference + agent pipeline) in one command
python scripts/pg-seed-data/export_fixtures.py

# Or export individual scopes:
python scripts/pg-seed-data/export_fixtures.py --scope reference  # fixtures/ only
python scripts/pg-seed-data/export_fixtures.py --scope agent      # agent-fixtures/ only
python scripts/pg-seed-data/export_fixtures.py --limit 500        # cap rows per table

# 3. Optionally regenerate schema.sql
docker exec postgres-server pg_dump -U postgres -d talent_finder \
  --schema-only --schema=dbo --no-owner --no-privileges \
  > scripts/pg-seed-data/schema_raw.sql
python scripts/pg-seed-data/clean_schema.py

# 5. Commit updated fixtures (include agent-fixtures when pipeline snapshot changed)
git add scripts/pg-seed-data/fixtures/ scripts/pg-seed-data/schema.sql
git add scripts/pg-seed-data/agent-fixtures/
git commit -m "Update PostgreSQL seed fixtures"
```
