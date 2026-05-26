# QA_DATA_CONTRACT.md — Ask-the-Data Schema Source of Truth

**Status:** Active · **Owner:** Pair C (BryanPMX + Milol0608)
**Tracks:** [#177](https://github.com/Building-With-Agents/job-intelligence-engine/issues/177) ·
consumed by [#171](https://github.com/Building-With-Agents/job-intelligence-engine/issues/171) (full `_SCHEMA_HINT` rewrite)
and [#186](https://github.com/Building-With-Agents/job-intelligence-engine/issues/186) (hotfix)

---

This document is the **single source of truth** for every table, column, join
path, and query pattern the Ask-the-Data SQL generator is authorised to use.
The `_SCHEMA_HINT` string in `analytics/query_engine/routing.py` must be
derived directly from this file — if a column is not here, the LLM must not
reference it.

All tables live in the `dbo` schema on PostgreSQL.
Reference as `dbo.<table_name>`.

---

## Table of Contents

1. [Canonical Join Paths](#1-canonical-join-paths)
2. [The 5 Extraction Dimensions (`extracted_intelligence` JSONB)](#2-the-5-extraction-dimensions)
3. [Weekly Aggregate Tables](#3-weekly-aggregate-tables)
4. [Column Quality — `job_postings`](#4-column-quality--job_postings)
5. [Canonical Query Recipes](#5-canonical-query-recipes)

---

## 1. Canonical Join Paths

The Q&A SQL generator is allowed to use exactly the following join paths.
Any other join is either unsupported, unmaintained, or produces incorrect
row counts.

### 1a. `job_postings` ↔ `normalized_jobs`

```sql
JOIN dbo.normalized_jobs nj
  ON jp.source      = nj.source
 AND jp.external_id = nj.external_id
```

| Detail | Value |
|--------|-------|
| `job_postings` columns | `source TEXT`, `external_id TEXT` (agent-added via migrations) |
| `normalized_jobs` columns | `source VARCHAR(50) NOT NULL`, `external_id VARCHAR(255) NOT NULL` |
| Index on `normalized_jobs` | `ix_normalized_jobs_source_eid (source, external_id)` |
| Index on `job_postings` | None yet — added by [#176](https://github.com/Building-With-Agents/job-intelligence-engine/issues/176) |
| **Null-coverage caveat** | ~22% of `job_postings` rows are legacy-seeded (pre-pipeline) with `source IS NULL` or `external_id IS NULL`. These rows have no `normalized_jobs` or `raw_ingested_jobs` lineage. Always `INNER JOIN` unless you explicitly intend to include legacy-only rows. |

### 1b. `normalized_jobs` → `raw_ingested_jobs`

```sql
JOIN dbo.raw_ingested_jobs rij
  ON nj.raw_job_id = rij.id
```

| Detail | Value |
|--------|-------|
| `normalized_jobs.raw_job_id` | `INTEGER NULLABLE` — null for records normalised before raw staging was introduced |
| `raw_ingested_jobs.id` | `SERIAL PRIMARY KEY` |
| Index on `raw_ingested_jobs` | `ix_raw_ingested_jobs_source_eid (source, external_id)` |
| Use case | Raw payload inspection only. Not needed for any standard Q&A query. |

### 1c. `job_postings` → `extracted_intelligence` (two-hop)

There is **no direct FK** from `job_postings` to `extracted_intelligence`.
Use the two-step join:

```sql
JOIN dbo.normalized_jobs nj
  ON jp.source      = nj.source
 AND jp.external_id = nj.external_id
JOIN dbo.extracted_intelligence ei
  ON ei.normalized_job_id = nj.id
```

| Detail | Value |
|--------|-------|
| `extracted_intelligence.normalized_job_id` | `INTEGER NOT NULL` FK → `dbo.normalized_jobs.id` |
| Index on `extracted_intelligence` | `ix_extracted_intelligence_norm_id (normalized_job_id)` |
| **Preferred alternative** | For skill/tool counts, always prefer `skill_demand_weekly` / `tool_demand_weekly` over a live JSONB unnest — the aggregates are pre-computed and indexed. |

### 1d. `job_postings` → `companies`

```sql
JOIN dbo.companies c ON jp.company_id = c.company_id::uuid
```

| Detail | Value |
|--------|-------|
| `job_postings.company_id` | UUID — always populated (enrichment guarantees a placeholder when no match) |
| `companies.company_id` | TEXT PK (cast required: `c.company_id::uuid` or `jp.company_id::text`) |

### 1e. `job_postings` → `canonical_roles`

```sql
JOIN dbo.canonical_roles cr ON jp.canonical_role_id = cr.role_id
```

| Detail | Value |
|--------|-------|
| `job_postings.canonical_role_id` | TEXT NULLABLE — populated after Week 7 canonical role clustering |
| `canonical_roles.role_id` | VARCHAR(64) UNIQUE |
| Use case | Resolve role label from `canonical_role_id`, or look up `top_skills`/`top_tools` per cluster |

---

## 2. The 5 Extraction Dimensions

All five dimensions are stored as JSONB arrays in `dbo.extracted_intelligence`.
Each row in `extracted_intelligence` corresponds to one `normalized_jobs` row.

**Golden rule:** For counting skills or tools across postings, **always use the
weekly aggregate tables** (`skill_demand_weekly`, `tool_demand_weekly`).
Direct JSONB unnest is expensive and appropriate only for row-level inspection
or dimensions that have no aggregate table (tasks, responsibilities, context).

---

### 2a. `skills`

**Column:** `extracted_intelligence.skills JSONB NOT NULL DEFAULT '[]'`
**Weekly aggregate:** `dbo.skill_demand_weekly` ✅ — prefer this for counts.

**Object shape:**

```json
{
  "skill_name":    "Python",
  "type":          "Technical",
  "confidence":    0.97,
  "required_flag": true,
  "skill_id":      "esco-uuid-or-null",
  "source_span": {
    "text":        "Python",
    "field_source":"description",
    "start_char":  42,
    "end_char":    48
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `skill_name` | string | Authoritative name — see `skills-naming.mdc`. Never use `label`. |
| `type` | `Technical\|Domain\|Soft\|Certification\|Tool` | Extraction category |
| `confidence` | float 0–1 | Extraction confidence |
| `required_flag` | bool or null | True = explicitly required in posting |
| `skill_id` | string or null | FK to `dbo.skills.skill_id` when taxonomy link succeeded |
| `source_span` | object | Evidence location in source text |

**Unnest pattern (row-level inspection only):**

```sql
SELECT jp.job_posting_id,
       elem->>'skill_name' AS skill_name,
       (elem->>'confidence')::float AS confidence
FROM dbo.job_postings jp
JOIN dbo.normalized_jobs nj
  ON jp.source = nj.source AND jp.external_id = nj.external_id
JOIN dbo.extracted_intelligence ei ON ei.normalized_job_id = nj.id,
     jsonb_array_elements(ei.skills) AS elem
WHERE (elem->>'confidence')::float >= 0.75
LIMIT 100;
```

**Top-N by count (use aggregate table instead):**

```sql
SELECT skill_label, posting_count
FROM dbo.skill_demand_weekly
WHERE week_start = (SELECT MAX(week_start) FROM dbo.skill_demand_weekly)
ORDER BY posting_count DESC
LIMIT 10;
```

---

### 2b. `tools`

**Column:** `extracted_intelligence.tools JSONB NOT NULL DEFAULT '[]'`
**Weekly aggregate:** `dbo.tool_demand_weekly` ✅ — prefer this for counts.

**Object shape:**

```json
{
  "tool_name":    "PostgreSQL",
  "category":    "database",
  "confidence":   0.97,
  "is_genai_tool": false,
  "tool_id":      "tool-postgres-or-null",
  "source_span": {
    "text":        "PostgreSQL",
    "field_source":"requirements",
    "start_char":  120,
    "end_char":    130
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `tool_name` | string | Canonical tool name |
| `category` | `language\|framework\|platform\|database\|devops\|ai_tool\|other` | Tool category |
| `confidence` | float 0–1 | Extraction confidence |
| `is_genai_tool` | bool | True for AI/ML-specific tools (LangChain, HuggingFace, etc.) |
| `tool_id` | string or null | Internal catalog identifier |
| `source_span` | object | Evidence location in source text |

**Top-N by count:**

```sql
SELECT tool_label, posting_count
FROM dbo.tool_demand_weekly
WHERE week_start = (SELECT MAX(week_start) FROM dbo.tool_demand_weekly)
ORDER BY posting_count DESC
LIMIT 10;
```

---

### 2c. `tasks`

**Column:** `extracted_intelligence.tasks JSONB NOT NULL DEFAULT '[]'`
**Weekly aggregate:** None — raw JSONB only.

**Object shape:**

```json
{
  "task_description": "Design and implement REST APIs for mobile clients",
  "task_category":    "technical",
  "seniority_signal": "mid",
  "confidence":       0.85,
  "source_span": {
    "text":        "Design and implement REST APIs",
    "field_source":"responsibilities",
    "start_char":  0,
    "end_char":    30
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `task_description` | string | Short actionable task phrase |
| `task_category` | `core\|supporting\|management\|technical` | Task type |
| `seniority_signal` | `entry\|mid\|senior\|lead\|any` | Implied seniority |
| `confidence` | float 0–1 | Extraction confidence |
| `source_span` | object | Evidence location in source text |

**Unnest pattern:**

```sql
SELECT elem->>'task_description' AS task,
       elem->>'task_category'    AS category,
       elem->>'seniority_signal' AS seniority
FROM dbo.extracted_intelligence ei,
     jsonb_array_elements(ei.tasks) AS elem
WHERE (elem->>'confidence')::float >= 0.75
LIMIT 100;
```

---

### 2d. `responsibilities`

**Column:** `extracted_intelligence.responsibilities JSONB NOT NULL DEFAULT '[]'`
**Weekly aggregate:** None — raw JSONB only.

**Object shape:**

```json
{
  "responsibility_description": "Own the end-to-end data pipeline architecture",
  "scope":                    "team",
  "requires_ai_competency":   false,
  "confidence":               0.88,
  "source_span": {
    "text":        "Own the end-to-end data pipeline architecture",
    "field_source":"description",
    "start_char":  200,
    "end_char":    245
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `responsibility_description` | string | High-level ownership statement |
| `scope` | `individual\|team\|department\|organization` | Impact scope |
| `requires_ai_competency` | bool | True when AI/ML competency is explicitly required |
| `confidence` | float 0–1 | Extraction confidence |
| `source_span` | object | Evidence location in source text |

---

### 2e. `context`

**Column:** `extracted_intelligence.context JSONB NOT NULL DEFAULT '[]'`
**Weekly aggregate:** None — Pass 1 pattern matching only (zero LLM cost).

**Object shape:**

```json
{
  "signal_type": "remote_policy",
  "value":       "hybrid",
  "confidence":  0.90,
  "source_span": {
    "text":        "hybrid work",
    "field_source":"description",
    "start_char":  315,
    "end_char":    326
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `signal_type` | `remote_policy\|team_size\|reporting_structure\|work_methodology\|ai_adoption_signal` | Signal category |
| `value` | string | Extracted value (e.g. `"hybrid"`, `"team of 8"`, `"Agile/Scrum"`) |
| `confidence` | float 0–1 | Pattern-match confidence |
| `source_span` | object | Evidence location in source text |

---

## 3. Weekly Aggregate Tables

These tables are pre-computed by the Analytics Agent and are the **preferred**
source for any count, trend, or salary question. They join back to `job_postings`
only via temporal period (`week_start`) — not via a direct FK.

### 3a. `skill_demand_weekly`

```
dbo.skill_demand_weekly (
    id             SERIAL PRIMARY KEY,
    skill_label    TEXT NOT NULL,        -- matches extracted_intelligence.skills[*].skill_name
    esco_uri       TEXT,                 -- ESCO taxonomy URI when linked; nullable
    week_start     DATE NOT NULL,
    posting_count  INTEGER NOT NULL,
    employer_count INTEGER NOT NULL DEFAULT 0,
    computed_at    TIMESTAMPTZ NOT NULL
)
UNIQUE (skill_label, week_start)
INDEX ix_skill_demand_weekly_week (week_start)
INDEX ix_skill_demand_weekly_skill (skill_label)
```

Typical cardinality: ~200–500 distinct skill labels per week.

### 3b. `tool_demand_weekly`

```
dbo.tool_demand_weekly (
    id             SERIAL PRIMARY KEY,
    tool_label     TEXT NOT NULL,        -- matches extracted_intelligence.tools[*].tool_name
    week_start     DATE NOT NULL,
    posting_count  INTEGER NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL
)
UNIQUE (tool_label, week_start)
INDEX ix_tool_demand_weekly_week (week_start)
INDEX ix_tool_demand_weekly_tool (tool_label)
```

### 3c. `role_snapshot_weekly`

```
dbo.role_snapshot_weekly (
    id                  SERIAL PRIMARY KEY,
    week_start          DATE NOT NULL,
    canonical_role_id   VARCHAR(64) NOT NULL REFERENCES dbo.canonical_roles(role_id),
    posting_count       INTEGER NOT NULL DEFAULT 0,
    role_title          TEXT,
    avg_salary          DOUBLE PRECISION,
    median_salary       DOUBLE PRECISION,
    salary_p25          DOUBLE PRECISION,
    salary_p50          DOUBLE PRECISION,
    salary_p75          DOUBLE PRECISION,
    salary_p95          DOUBLE PRECISION,
    top_skills          JSONB,           -- list of skill_name strings
    top_tools           JSONB,           -- list of tool_name strings
    computed_at         TIMESTAMPTZ
)
UNIQUE (week_start, canonical_role_id)
INDEX ix_role_snapshot_weekly_week_start (week_start)
INDEX ix_role_snapshot_weekly_canonical_role_id (canonical_role_id)
```

Note: `avg_salary` is derived from structured salary fields and may be null when
`salary_range` was not parseable. Use `median_salary` (p50) for reporting.

### 3d. `sector_summary_weekly`

```
dbo.sector_summary_weekly (
    id             SERIAL PRIMARY KEY,
    week_start     DATE NOT NULL,
    sector         TEXT NOT NULL,        -- matches industry_sectors.sector_title
    posting_count  INTEGER NOT NULL,
    employer_count INTEGER NOT NULL,
    avg_salary     DOUBLE PRECISION,     -- stores p50 (median); may be null
    top_skills     JSONB                 -- list of up to 10 skill_name strings
)
```

### 3e. `geo_demand_weekly`

```
dbo.geo_demand_weekly (
    id                    SERIAL PRIMARY KEY,
    week_start            DATE NOT NULL,
    borderplex_subregion  VARCHAR(32) NOT NULL,  -- matches job_postings.borderplex_subregion
    posting_count         INTEGER NOT NULL
)
```

Known `borderplex_subregion` values come from `enrichment/classifiers/borderplex_subregion.py`.
Use this table for geographic demand questions scoped to the El Paso–Juárez Borderplex.

### 3f. `skill_velocity` (trend)

```
dbo.skill_velocity (
    id                   SERIAL PRIMARY KEY,
    skill_label          TEXT NOT NULL,
    esco_uri             TEXT,
    week                 DATE NOT NULL,     -- note: column name is "week", not "week_start"
    demand_count         INTEGER NOT NULL,
    week_over_week_change DOUBLE PRECISION NOT NULL,
    four_week_trend      TEXT NOT NULL,     -- "rising" | "falling" | "stable"
    trend_confidence     DOUBLE PRECISION NOT NULL
)
UNIQUE (skill_label, week)
```

Use `week_over_week_change` for "trending this week vs last week" questions.
Use `four_week_trend` for longer-term direction.

### 3g. `skill_co_occurrence`

```
dbo.skill_co_occurrence (
    id                 SERIAL PRIMARY KEY,
    skill_a            TEXT NOT NULL,
    skill_b            TEXT NOT NULL,
    co_occurrence_count INTEGER NOT NULL,
    week_start         DATE NOT NULL,
    computed_at        TIMESTAMPTZ NOT NULL
)
UNIQUE (skill_a, skill_b, week_start)
```

Use for "what skills appear together with X" questions.

### 3h. `posting_freshness`

```
dbo.posting_freshness (
    posting_id    TEXT PRIMARY KEY,      -- job_posting_id as text
    first_seen    TIMESTAMPTZ NOT NULL,
    last_seen     TIMESTAMPTZ NOT NULL,
    duration_days INTEGER NOT NULL,
    is_repost     BOOLEAN NOT NULL DEFAULT FALSE,
    repost_count  INTEGER NOT NULL DEFAULT 0,
    fill_proxy    BOOLEAN NOT NULL DEFAULT FALSE,
    computed_at   TIMESTAMPTZ NOT NULL
)
```

Use for "how long are postings staying open" or "what % are reposts" questions.

---

## 4. Column Quality — `job_postings`

`dbo.job_postings` is the canonical enriched job record. It has two column
origins: the legacy Prisma schema and agent-added columns via `migrations.py`.

**Legend:**
- ✅ Populated and safe to query
- ⚠️ Partially available — read the note before using
- ❌ Deprecated — 99%+ NULL or never written; must never appear in generated SQL

### 4a. Identity & Linking

| Column | Type | Status | Q&A Guidance |
|--------|------|--------|--------------|
| `job_posting_id` | UUID PK | ✅ Always set | Primary key; cast to text when joining to `posting_freshness` |
| `company_id` | UUID FK | ✅ Always set | Resolved via companies table at enrichment time; safe to `JOIN dbo.companies` |
| `source` | TEXT | ✅ Populated for pipeline rows | Part of composite join key to `normalized_jobs`; e.g. `'jsearch'` |
| `external_id` | TEXT | ✅ Populated for pipeline rows | Part of composite join key to `normalized_jobs` |
| `ingestion_run_id` | TEXT | ✅ | Ties row to `job_ingestion_runs.run_id`; use for batch-level analysis |
| `canonical_role_id` | TEXT FK | ✅ When clustering has run | FK to `canonical_roles.role_id`; null for pre-Week-7 rows |
| `employer_profile_id` | UUID FK | ✅ When enrichment ran | FK to `employer_profiles.id` |

### 4b. Job Content

| Column | Type | Status | Q&A Guidance |
|--------|------|--------|--------------|
| `job_title` | VARCHAR(255) | ✅ Always set | Raw job title string; not normalised — use `canonical_roles.label` for role dimension queries |
| `job_description` | TEXT | ✅ | Full posting text; too large for Q&A aggregation |
| `employment_type` | VARCHAR(255) | ✅ | e.g. `'full-time'`, `'contract'` |
| `location` | NVARCHAR(255) | ✅ | Unstructured text blob (e.g. `"El Paso, TX, USA"`); use `borderplex_subregion` for geo filtering |
| `salary_range` | VARCHAR(45) | ⚠️ Text blob | e.g. `"45000.0-75000.0"` or `"N/A"`; not reliable for numeric salary queries — use `role_snapshot_weekly.median_salary` instead |
| `status` | TEXT | ✅ | Default `'open'`; filter `WHERE status = 'open'` for active postings |
| `job_post_url` | VARCHAR(255) | ✅ Nullable | Source URL of the posting |

### 4c. Temporal Columns

| Column | Type | Status | Q&A Guidance |
|--------|------|--------|--------------|
| `createdat` | TIMESTAMPTZ | ✅ **Ingestion time** — always set | Use as recency proxy for "latest data" filters. This is when the row entered the database, NOT when the job was posted. |
| `date_posted` | — | ⚠️ **NOT YET on `job_postings`** | Lives on `normalized_jobs.date_posted`. Issue [#170](https://github.com/Building-With-Agents/job-intelligence-engine/issues/170) will promote it. Until then: join `normalized_jobs` to get this value, or use `createdat` as a proxy. |
| `publish_date` | DateTime | ❌ **DEPRECATED** — 99.26% NULL | Legacy Prisma field; never written by the pipeline. **Must not appear in generated SQL.** |
| `temporal_period` | TEXT | ✅ | Enrichment-derived period string (e.g. `'2026-W15'`); use for grouping by week |

### 4d. Geographic & Classification

| Column | Type | Status | Q&A Guidance |
|--------|------|--------|--------------|
| `borderplex_subregion` | TEXT | ✅ When enrichment ran | Use for Borderplex geographic filtering; matches `geo_demand_weekly.borderplex_subregion` |
| `county` | VARCHAR(255) | ⚠️ Partially NULL | Legacy Prisma field; populated for seeded rows but not reliably for pipeline rows |
| `zip` | VARCHAR(45) | ⚠️ Legacy | Use `zip_code` instead |
| `zip_code` | VARCHAR(10) | ✅ | Normalised ZIP resolved during normalization via `postal_geo_data` |
| `soc_code` | TEXT | ✅ When SOC enrichment ran | SOC-2018 occupational code; use for occupational category queries |
| `naics_code` | TEXT | ✅ | NAICS-2022 industry code; `'unknown'` when classifier was uncertain |
| `sector_id` | UUID FK | ✅ When sector resolved | FK to `industry_sectors.industry_sector_id`; use `sector_summary_weekly` for aggregation |

### 4e. Quality & Spam

| Column | Type | Status | Q&A Guidance |
|--------|------|--------|--------------|
| `spam_tier` | TEXT | ✅ | `'clean'` \| `'flagged'` \| `'rejected'` \| `'uncertain'`. Filter `WHERE spam_tier = 'clean'` for high-quality rows. |
| `is_spam` | BOOLEAN | ✅ | TRUE = auto-rejected; NULL = flagged for review; FALSE = clean |
| `spam_score` | DOUBLE PRECISION | ✅ | 0–1; < 0.7 = clean, 0.7–0.9 = flagged, > 0.9 = rejected |
| `quality_score` | DOUBLE PRECISION | ✅ | 0–1 overall posting quality |
| `overall_confidence` | DOUBLE PRECISION | ✅ | 0–1 enrichment confidence |
| `field_confidence` | JSONB | ✅ | Per-field confidence scores; not useful for standard Q&A |
| `ai_relevance_score` | DOUBLE PRECISION | ✅ Nullable | AI/ML relevance signal |

### 4f. Deduplication (Internal — do not use in Q&A SQL)

| Column | Type | Status | Q&A Guidance |
|--------|------|--------|--------------|
| `is_duplicate` | BOOLEAN DEFAULT FALSE | ✅ | Filter `WHERE NOT is_duplicate` to exclude duplicate postings from counts |
| `duplicate_cluster_id` | UUID | ✅ Nullable | Groups duplicate postings; not needed for standard Q&A |
| `dedup_text_hash` | TEXT | ✅ Internal | Used by dedup pipeline only — do not query directly |
| `dedup_embedding` | vector(1536) | ✅ Internal | pgvector embedding for similarity dedup — do not query directly |

### 4g. Deprecated Columns — Never Query

These columns exist in the database schema but must not appear in any
generated SQL. The LLM should never be given their names.

| Column | NULL % | Reason |
|--------|--------|--------|
| `employer_id` | 100% | Legacy Prisma FK — never written by the pipeline |
| `tech_area_id` | 100% | Legacy Prisma FK — never written by the pipeline |
| `start_date` | 100% | Legacy Prisma — never written |
| `end_date` | 100% | Legacy Prisma — never written |
| `location_id` | 99.26% | Legacy Prisma FK to company_addresses — pipeline stores location directly |
| `publish_date` | 99.26% | Legacy Prisma — pipeline uses `createdat`; posting date will come from `date_posted` (#170) |
| `occupation_code` | ⚠️ Variable | Legacy field; superseded by `soc_code` — use `soc_code` |

---

## 5. Canonical Query Recipes

These three recipes define the SQL the Ask-the-Data SQL generator must
be able to produce correctly. The `_SCHEMA_HINT` in `routing.py` is
derived from these patterns; regression tests in `analytics/tests/`
validate against them.

---

### Recipe 1 — "What roles show the highest posting volume in the latest data?"

**Target table:** `role_snapshot_weekly` (pre-aggregated, indexed)

```sql
SELECT rsw.role_title,
       rsw.posting_count,
       rsw.week_start
FROM dbo.role_snapshot_weekly rsw
WHERE rsw.week_start = (
    SELECT MAX(week_start) FROM dbo.role_snapshot_weekly
)
ORDER BY rsw.posting_count DESC
LIMIT 10;
```

**Why this works:**
- `role_snapshot_weekly` is the authoritative weekly aggregate per canonical role.
- `week_start` uses `MAX(week_start)` subquery — no hardcoded date.
- `role_title` is the human-readable label stored at snapshot time (denormalized from `canonical_roles.label`).

**Anti-pattern (must not generate):**
```sql
-- WRONG: job_postings has no aggregation path to role by posting volume without canonical_role_id
SELECT job_title, COUNT(*) FROM dbo.job_postings GROUP BY job_title ORDER BY COUNT(*) DESC LIMIT 10;
-- This produces noise — raw job titles are not normalised.
```

---

### Recipe 2 — "What skills are trending this week vs last week?"

**Target table:** `skill_velocity` (pre-computed WoW delta)

```sql
SELECT sv.skill_label,
       sv.demand_count,
       sv.week_over_week_change,
       sv.four_week_trend
FROM dbo.skill_velocity sv
WHERE sv.week = (SELECT MAX(week) FROM dbo.skill_velocity)
ORDER BY sv.week_over_week_change DESC
LIMIT 10;
```

**Fallback if `skill_velocity` is empty** (use two-week join on `skill_demand_weekly`):

```sql
WITH latest AS (
    SELECT MAX(week_start) AS wk FROM dbo.skill_demand_weekly
),
current_week AS (
    SELECT skill_label, posting_count
    FROM dbo.skill_demand_weekly, latest
    WHERE week_start = latest.wk
),
prior_week AS (
    SELECT skill_label, posting_count
    FROM dbo.skill_demand_weekly, latest
    WHERE week_start = (latest.wk - INTERVAL '7 days')::date
)
SELECT c.skill_label,
       c.posting_count                              AS current_count,
       COALESCE(p.posting_count, 0)                AS prior_count,
       c.posting_count - COALESCE(p.posting_count, 0) AS wow_change
FROM current_week c
LEFT JOIN prior_week p USING (skill_label)
ORDER BY wow_change DESC
LIMIT 10;
```

**Anti-pattern (must not generate):**
```sql
-- WRONG: job_postings has no skill_id column — skills are in extracted_intelligence
SELECT skill_id, COUNT(*) FROM dbo.job_postings GROUP BY skill_id;
-- This will error: column "skill_id" does not exist on dbo.job_postings
```

---

### Recipe 3 — "Median salary for data analyst roles in the Borderplex region"

**Target table:** `role_snapshot_weekly` for salary; note Borderplex filter limitation.

```sql
SELECT rsw.role_title,
       rsw.median_salary,
       rsw.salary_p25,
       rsw.salary_p75,
       rsw.week_start
FROM dbo.role_snapshot_weekly rsw
JOIN dbo.canonical_roles cr ON cr.role_id = rsw.canonical_role_id
WHERE LOWER(cr.label) LIKE '%data analyst%'
  AND rsw.week_start = (SELECT MAX(week_start) FROM dbo.role_snapshot_weekly)
ORDER BY rsw.week_start DESC
LIMIT 5;
```

**Known limitation:** `role_snapshot_weekly` does not yet carry a
`borderplex_subregion` column — that dimension lives on `job_postings`. The
Borderplex salary filter requires a more expensive join:

```sql
SELECT cr.label          AS role_title,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY nj.salary_min) AS median_salary_min,
       COUNT(DISTINCT jp.job_posting_id)                           AS posting_count
FROM dbo.job_postings jp
JOIN dbo.normalized_jobs nj
  ON jp.source = nj.source AND jp.external_id = nj.external_id
JOIN dbo.canonical_roles cr ON jp.canonical_role_id = cr.role_id
WHERE LOWER(cr.label)         LIKE '%data analyst%'
  AND jp.borderplex_subregion IS NOT NULL
  AND jp.spam_tier             = 'clean'
  AND nj.salary_min            IS NOT NULL
GROUP BY cr.label
ORDER BY median_salary_min DESC
LIMIT 5;
```

This join is correct but slower (no index on `date_posted` until [#176](https://github.com/Building-With-Agents/job-intelligence-engine/issues/176) lands).
The LLM should prefer `role_snapshot_weekly` when the Borderplex filter is not
strictly required, and note the caveat in its answer.

---

## Appendix — Reference Tables

These tables are safe to JOIN for dimension lookups. They are seeded at
pipeline startup and treated as agent-owned reference data.

| Table | PK | Key query columns | Notes |
|-------|----|-------------------|-------|
| `canonical_roles` | `role_id VARCHAR(64)` | `label, role_family, description, posting_count, top_skills, top_tools` | Cluster-level role definitions; `role_family` groups ~200 labels into domain buckets (JIE #362). Job-count-by-domain: `JOIN job_postings ON canonical_role_id = role_id` + `GROUP BY role_family` (non-null `canonical_role_id` only; see #363). |
| `companies` | `company_id TEXT` | `company_name, size, city, state, industry_sector_id` | Cast `job_postings.company_id` to `TEXT` when joining |
| `industry_sectors` | `industry_sector_id TEXT` | `sector_title` | Join via `job_postings.sector_id` or `sector_summary_weekly.sector` |
| `employer_profiles` | `id UUID` | `company_size, ai_maturity_signal, sector, is_known_employer` | Join via `job_postings.employer_profile_id` |

## Appendix — Tables Excluded from Q&A SQL

The SQL generator must **never** reference these tables. They are internal,
write-only, or contain PII-adjacent data.

| Table | Reason |
|-------|--------|
| `raw_ingested_jobs` | Raw staging — unvalidated content, no Q&A value |
| `normalization_quarantine` | Failed records — not enriched |
| `llm_audit_log` | Internal cost/audit log |
| `orchestration_audit_log` | Internal orchestration log |
| `cohort_gap_cache` | Internal API cache |
| `analytics_pipeline_state` | Singleton watermark — not queryable |
| `trajectory_map` | Phase 2 scaffold — empty |
| `socc` | SOC code reference — use `job_postings.soc_code` directly |
| `naics` | NAICS reference — use `job_postings.naics_code` directly |
| `postal_geo_data` | ZIP lookup — use `job_postings.zip_code` directly |
| `skills` | Taxonomy store with pgvector embeddings — use `skill_demand_weekly` for counts |
| `technology_areas` | Reference taxonomy — use `tool_demand_weekly` for counts |

---

*This document tracks issue [#177](https://github.com/Building-With-Agents/job-intelligence-engine/issues/177).
Update it whenever the schema, aggregate tables, or extraction dimensions change —
then re-derive `_SCHEMA_HINT` in `analytics/query_engine/routing.py` accordingly.*
