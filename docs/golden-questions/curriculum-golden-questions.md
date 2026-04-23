# Curriculum Golden Questions

These questions are **skill-frequency** curriculum checks: “what should someone learn to qualify for this work?” is answered from **which extracted skills appear most often** in Borderplex IT postings for that theme—not from a separate role-blueprint table.

**Source tables:** `job_postings` → `normalized_jobs` (`source`, `external_id`) → `extracted_intelligence` (JSONB `skills`: `skill_name`, optional `skill_id`) → optional join to `skills` for taxonomy. **`canonical_roles` is not used** (fixtures have no role-cluster rows; frequency over postings is enough for demos).

**Guards:** `role_classification != 'N/A Not an IT role'` on all posting rows (**#197**). Borderplex filter: `borderplex_subregion` and/or `location` / `county` text patterns.

**Verify:** `python scripts/verify_curriculum_golden_questions_data.py` (seed fixtures below; set `PYTHON_DATABASE_URL` for live SQL probe).

### Verification (seed fixtures) — 10/10 PASS

| Q | Status | Postings | EI rows | Skill lines | Taxonomy hits≈ |
|---|--------|----------|---------|-------------|----------------|
| 1 | **PASS** | 410 | 358 | 4712 | 2265 |
| 2 | **PASS** | 391 | 309 | 4264 | 1711 |
| 3 | **PASS** | 364 | 289 | 4287 | 1863 |
| 4 | **PASS** | 575 | 438 | 6111 | 2763 |
| 5 | **PASS** | 412 | 297 | 3345 | 1603 |
| 6 | **PASS** | 122 | 97 | 1628 | 686 |
| 7 | **PASS** | 595 | 450 | 6500 | 3177 |
| 8 | **PASS** | 724 | 591 | 8198 | 3231 |
| 9 | **PASS** | 345 | 276 | 3478 | 1512 |
| 10 | **PASS** | 666 | 564 | 7629 | 3004 |

| # | Question | Data verified | Notes |
|---|----------|---------------|-------|
| 1 | What **skills** (languages, data tools, cloud, AI-assistant–related) appear **most frequently** in **data engineering** job postings in the Borderplex? | **PASS** | Theme match on title/description/`role_classification`; aggregate `skill_name` from EI. |
| 2 | What **LLM frameworks, integration-style skills, and related capabilities** appear **most often** in Borderplex postings that look like **AI / agentic** software work? | **PASS** | Same pattern: frequency over themed postings. |
| 3 | What **certifications, tools, and security skills** appear **most often** in Borderplex **cybersecurity** job postings? | **PASS** | |
| 4 | What **cloud platforms, certification-related signals, and cloud skills** appear **most frequently** in Borderplex **cloud architect / cloud engineer** postings? | **PASS** | |
| 5 | What **frameworks, testing tools, and front-end skills** appear **most often** in Borderplex **frontend / web** development postings? | **PASS** | |
| 6 | What **deployment, monitoring, and MLOps-related skills** appear **most frequently** in Borderplex **MLOps and ML-infrastructure** postings? | **PASS** | |
| 7 | What **IaC, observability, and platform skills** appear **most often** in Borderplex **DevOps and SRE** postings? | **PASS** | |
| 8 | What **skills** appear **most often** in Borderplex **help desk, systems administration, and network** IT support postings (useful for curriculum beyond common cert baselines)? | **PASS** | |
| 9 | What **languages, compliance-related terms, and fintech-relevant skills** appear **most frequently** in Borderplex **fintech and payments** postings? | **PASS** | Theme + optional `naics_code` 52* in verifier. |
| 10 | What **platforms, health-informatics / regulatory terms, and skills** appear **most often** in Borderplex **healthcare IT and clinical data** postings? | **PASS** | |

**Example SQL — top extracted skills for a themed slice** (add #197 to `WHERE` as in the verification script):

```sql
WITH themed AS (
  SELECT nj.id AS norm_id
  FROM dbo.job_postings jp
  INNER JOIN dbo.normalized_jobs nj
    ON nj.source = jp.source AND nj.external_id = jp.external_id
  WHERE
    (jp.borderplex_subregion IS NOT NULL
     OR LOWER(COALESCE(jp.location, '')) ~* 'el[[:space:]]*paso|las[[:space:]]*cruces|juarez|santa[[:space:]]*teresa|border|sunland')
    AND TRIM(COALESCE(jp.role_classification, '')) IS DISTINCT FROM 'N/A Not an IT role'  /* #197 */
    AND (jp.job_title || ' ' || COALESCE(jp.job_description, '')) ~* 'data engineer|etl|snowflake|dbt'  /* example: data eng */
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

Join `sk.elem->>'skill_id'` to `dbo.skills` when the extractor populated IDs.

1. What **skills** (languages, data tools, cloud, AI-assistant–related) appear **most frequently** in **data engineering** job postings in the Borderplex?
2. What **LLM frameworks, integration-style skills, and related capabilities** appear **most often** in Borderplex postings that look like **AI / agentic** software work?
3. What **certifications, tools, and security skills** appear **most often** in Borderplex **cybersecurity** job postings?
4. What **cloud platforms, certification-related signals, and cloud skills** appear **most frequently** in Borderplex **cloud architect / cloud engineer** postings?
5. What **frameworks, testing tools, and front-end skills** appear **most often** in Borderplex **frontend / web** development postings?
6. What **deployment, monitoring, and MLOps-related skills** appear **most frequently** in Borderplex **MLOps and ML-infrastructure** postings?
7. What **IaC, observability, and platform skills** appear **most often** in Borderplex **DevOps and SRE** postings?
8. What **skills** appear **most often** in Borderplex **help desk, systems administration, and network** IT support postings (useful for curriculum beyond common cert baselines)?
9. What **languages, compliance-related terms, and fintech-relevant skills** appear **most frequently** in Borderplex **fintech and payments** postings?
10. What **platforms, health-informatics / regulatory terms, and skills** appear **most often** in Borderplex **healthcare IT and clinical data** postings?
