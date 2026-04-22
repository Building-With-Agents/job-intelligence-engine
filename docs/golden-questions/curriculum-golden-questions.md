# Curriculum Golden Questions

**Data verified:** 2026-04-22. The live schema does **not** use a `job_posting_skills` / `canonical_role_skills` join table in the pipeline export. Skill demand is verified via:

- `job_postings` (Borderplex filter: `borderplex_subregion` and/or `location`/`county` text) plus theme match on `job_title` / `job_description` / `role_classification`;
- `normalized_jobs` on (`source`, `external_id`) → `extracted_intelligence` (JSONB `skills` with `skill_name`, optional `skill_id`);
- `skills` taxonomy: match `skill_id` or normalized `skill_name` to `dbo.skills`.

`canonical_roles` is **empty in the current seed** (`scripts/pg-seed-data/fixtures/canonical_roles.json`); after analytics clustering, use `job_postings.canonical_role_id` and `dbo.canonical_roles` (`label`, `top_skills` JSONB).

Re-run: `python scripts/verify_curriculum_golden_questions_data.py` (with `PYTHON_DATABASE_URL` for live SQL probe). Counts below are from the same logic on seed fixtures.

| # | Question | Data verified | Notes |
|---|----------|---------------|-------|
| 1 | What should a data engineering training program cover given current Borderplex data-engineer postings — which languages, tools, cloud platforms, and AI-assistant skills should form the core? | **PARTIAL** | `job_postings`+`extracted_intelligence`+`skills` sufficient (e.g. **488** themed postings, **414** EI rows, **~5.6k** skill lines, **~2.6k** taxonomy hits). **Thin table:** `canonical_roles` = 0 rows in seed. |
| 2 | What should an AI agent developer training program cover given current Borderplex postings — which LLM frameworks (LangChain, LangGraph), orchestration patterns, and integration skills are in demand? | **PARTIAL** | Strong EI+skills; **524** / **404** / **~5.8k** / **~2.3k**. **Thin:** `canonical_roles`. |
| 3 | What should a cybersecurity training program cover given current Borderplex cybersecurity postings — which certifications, tools, and AI-augmentation skills should be included? | **PARTIAL** | **515** / **388** / **~6.0k** / **~2.5k**. **Thin:** `canonical_roles`. |
| 4 | What should a cloud-architect training program cover given current Borderplex cloud-architect postings — which cloud providers, certifications, and AI-integration skills should be prioritized? | **PARTIAL** | **738** / **540** / **~7.9k** / **~3.5k**. **Thin:** `canonical_roles`. |
| 5 | What should a frontend web development training program cover given current Borderplex frontend postings — which frameworks, testing tools, and AI-assisted development skills are most in demand? | **PARTIAL** | **470** / **333** / **~3.8k** / **~1.8k**. **Thin:** `canonical_roles`. |
| 6 | What should an MLOps training program cover given current Borderplex MLOps and ML-infrastructure postings — which deployment patterns, monitoring tools, and model-ops skills should be included? | **PARTIAL** | **152** / **120** / **~2.1k** / **~855** (smallest themed slice but still ≥10 for postings and EI). **Thin:** `canonical_roles`. |
| 7 | What should a DevOps and site-reliability training program cover given current Borderplex DevOps and SRE postings — which IaC tools, observability platforms, and AI-assisted operations skills are essential? | **PARTIAL** | **745** / **546** / **~8.1k** / **~3.9k**. **Thin:** `canonical_roles`. |
| 8 | What should an IT support training program cover given current Borderplex help-desk, systems-admin, and network-engineer postings — which skills go beyond the CompTIA A+ / Network+ baseline? | **PARTIAL** | **1097** / **882** / **~12.7k** / **~4.8k**. **Thin:** `canonical_roles`. |
| 9 | What should a fintech developer training program cover given current Borderplex fintech and payments-technology postings — which languages, compliance frameworks, and AI-adjacent skills should be included? | **PARTIAL** | Theme + optional `naics_code` 52*; **519** / **418** / **~5.6k** / **~2.3k**. **Thin:** `canonical_roles`. |
| 10 | What should a healthcare-IT training program cover given current Borderplex EHR-analyst, clinical-data-analyst, and health-informatics postings — which platforms, regulatory knowledge, and AI-adjacent skills should form the core? | **PARTIAL** | **977** / **813** / **~11.6k** / **~4.4k**. **Thin:** `canonical_roles`. |

**Example SQL (PostgreSQL) — top extracted skill names for a Borderplex+theme slice** (adjust `~*` pattern per track):

```sql
WITH themed AS (
  SELECT nj.id AS norm_id
  FROM dbo.job_postings jp
  INNER JOIN dbo.normalized_jobs nj
    ON nj.source = jp.source AND nj.external_id = jp.external_id
  WHERE
    (jp.borderplex_subregion IS NOT NULL
     OR LOWER(COALESCE(jp.location, '')) ~* 'el[[:space:]]*paso|las[[:space:]]*cruces|juarez|santa[[:space:]]*teresa|border|sunland')
  AND (jp.job_title || ' ' || COALESCE(jp.job_description, '')) ~* 'data engineer|etl|snowflake|dbt|spark'  -- example: data eng
)
SELECT sk.elem->>'skill_name' AS skill_name, COUNT(*) AS n
FROM themed t
INNER JOIN dbo.extracted_intelligence ei ON ei.normalized_job_id = t.norm_id
  AND (ei.extraction_failed IS NULL OR ei.extraction_failed = FALSE)
CROSS JOIN LATERAL jsonb_array_elements(ei.skills::jsonb) AS sk(elem)
WHERE sk.elem->>'skill_name' IS NOT NULL
GROUP BY 1
ORDER BY n DESC
LIMIT 25;
```

To align extractions with the taxonomy, join `sk.elem->>'skill_id'` to `dbo.skills.skill_id` when the extractor populated IDs.

1. What should a data engineering training program cover given current Borderplex data-engineer postings — which languages, tools, cloud platforms, and AI-assistant skills should form the core?
2. What should an AI agent developer training program cover given current Borderplex postings — which LLM frameworks (LangChain, LangGraph), orchestration patterns, and integration skills are in demand?
3. What should a cybersecurity training program cover given current Borderplex cybersecurity postings — which certifications, tools, and AI-augmentation skills should be included?
4. What should a cloud-architect training program cover given current Borderplex cloud-architect postings — which cloud providers, certifications, and AI-integration skills should be prioritized?
5. What should a frontend web development training program cover given current Borderplex frontend postings — which frameworks, testing tools, and AI-assisted development skills are most in demand?
6. What should an MLOps training program cover given current Borderplex MLOps and ML-infrastructure postings — which deployment patterns, monitoring tools, and model-ops skills should be included?
7. What should a DevOps and site-reliability training program cover given current Borderplex DevOps and SRE postings — which IaC tools, observability platforms, and AI-assisted operations skills are essential?
8. What should an IT support training program cover given current Borderplex help-desk, systems-admin, and network-engineer postings — which skills go beyond the CompTIA A+ / Network+ baseline?
9. What should a fintech developer training program cover given current Borderplex fintech and payments-technology postings — which languages, compliance frameworks, and AI-adjacent skills should be included?
10. What should a healthcare-IT training program cover given current Borderplex EHR-analyst, clinical-data-analyst, and health-informatics postings — which platforms, regulatory knowledge, and AI-adjacent skills should form the core?
