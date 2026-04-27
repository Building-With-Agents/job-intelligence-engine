# PostgreSQL Seed Data

Seed a fresh PostgreSQL container with reference data for the watechcoalition platform.

## Prerequisites: Git LFS

Five fixtures (`extracted_intelligence.json`, `raw_ingested_jobs.json`,
`job_postings.json`, `normalized_jobs.json`, `llm_audit_log.json`) are stored
via [Git LFS](https://git-lfs.com/) because they exceed GitHub's 50 MB
recommendation. **Install Git LFS once before cloning** or your seed will
silently load empty arrays from 133-byte pointer files:

```bash
# Install (one-time, system-level)
git lfs install                     # macOS / Linux (with git-lfs already on PATH)
# Windows: included with Git for Windows; if missing, run: winget install GitHub.GitLFS

# If you already cloned without LFS, fetch the real files:
git lfs pull
```

Verify with `ls -la scripts/pg-seed-data/fixtures/extracted_intelligence.json` —
you should see ~120 MB, not ~133 bytes.

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

**`seed_pg_database.py`** is **idempotent**: existing records are never overwritten or deleted. New fixture records are added automatically. Seeds both reference data and agent pipeline data from `fixtures/*.json` (single directory — see [File Structure](#file-structure)) in one command.

For most tables, conflicts use `ON CONFLICT DO NOTHING` (existing rows are skipped). For tables listed in `UPSERT_UPDATE_COLUMNS` (currently `job_postings`), conflicts trigger `DO UPDATE SET col = COALESCE(target.col, EXCLUDED.col)` for a configured column subset — so re-seeding after a schema-adds-columns migration **fills the new columns on existing rows without clobbering any locally-populated state**. Devs do not need to wipe their volume to pick up new column data.

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
- **Agent pipeline rows** — `seed_pg_database.py` creates agent tables via `run_migrations()` and loads pipeline data from `fixtures/*.json` automatically (the agent tables share the same fixtures directory as reference tables)
- **Skill embeddings** — the `embedding` column is excluded from fixtures (107MB of pgvector data). Regenerate via the admin tool if needed.

## How It Works

### `seed_pg_database.py` (one command seeds everything)

Idempotent — safe to re-run at any time. Never deletes or overwrites existing data.

1. **Checks schema** — if dbo schema has no tables (fresh DB), runs `schema.sql` DDL; otherwise skips
2. **Runs agent migrations** — creates agent-managed tables + adds agent-owned columns to `job_postings`
3. **Loads reference fixtures** — `INSERT ... ON CONFLICT` from `fixtures/*.json` in FK-safe tier order. `DO NOTHING` for most tables; `DO UPDATE SET col = COALESCE(target.col, EXCLUDED.col)` for tables in `UPSERT_UPDATE_COLUMNS`.
4. **Loads agent pipeline data** — calls `seed_agent_data.py` internally (same upsert behavior, same `fixtures/` directory)

### `seed_agent_data.py` (called automatically, can also run standalone)

Loads agent pipeline tables from `fixtures/*.json` into staging/enrichment tables (and additional `job_postings` rows). Conflict behavior follows the same `UPSERT_UPDATE_COLUMNS` override map as `seed_pg_database.py`. Row counts are documented in `fixtures/metadata.json`.

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
  fixtures/                     ← Single source of truth — both reference + agent pipeline tables
    metadata.json               ← Export metadata with row counts (per-scope sections)
    Reference (40 tables, ~56k rows):
      skills.json               ← 5,683 skills (embeddings excluded)
      cip.json, socc.json, postal_geo_data.json, ...
    Agent pipeline (10 tables):
      raw_ingested_jobs.json    ← Ingested job data
      normalized_jobs.json      ← Normalized records
      extracted_intelligence.json ← Extraction results
      job_postings.json         ← Enriched postings (UPSERT-with-COALESCE on the 8 Q&A-ready columns)
      job_ingestion_runs.json, normalization_quarantine.json, employer_profiles.json, companies.json, naics.json, llm_audit_log.json
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

# Or export individual scopes (all output to fixtures/):
python scripts/pg-seed-data/export_fixtures.py --scope reference  # 40 reference tables only
python scripts/pg-seed-data/export_fixtures.py --scope agent      # 10 agent pipeline tables only
python scripts/pg-seed-data/export_fixtures.py --limit 500        # cap rows per table

# 3. Optionally regenerate schema.sql
docker exec postgres-server pg_dump -U postgres -d talent_finder \
  --schema-only --schema=dbo --no-owner --no-privileges \
  > scripts/pg-seed-data/schema_raw.sql
python scripts/pg-seed-data/clean_schema.py

# 5. Commit updated fixtures
git add scripts/pg-seed-data/fixtures/ scripts/pg-seed-data/schema.sql
git commit -m "Update PostgreSQL seed fixtures"
```

### Adding a column to `UPSERT_UPDATE_COLUMNS`

When a new migration adds columns to an existing seeded table and you want
re-seeding to fill those columns on existing rows (without clobbering local
state), edit the `UPSERT_UPDATE_COLUMNS` map in **both** `seed_pg_database.py`
and `seed_agent_data.py` to add the new column names:

```python
UPSERT_UPDATE_COLUMNS: dict[str, list[str]] = {
    "job_postings": [
        "date_posted", "seniority_level", "is_remote",
        "role_classification",
        "salary_min", "salary_max", "salary_currency", "salary_period",
        # add new columns here
    ],
}
```

The two maps must stay in sync (verified by `scripts/tests/test_seed_upsert_on_conflict.py`).
The COALESCE pattern guarantees existing non-NULL values are preserved.
