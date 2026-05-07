# Geographic Borderplex — Q&A baseline (#345)

## Snapshot header

| Field | Value |
|-------|--------|
| **Date** | 2026-05-04 |
| **Branch / SHA** | `fix/week10-eval-baseline-340-344-345` (see git at commit time) |
| **DB provenance** | Local / SoT PostgreSQL via `PYTHON_DATABASE_URL`. |
| **`job_postings` approx. count** | *Run on your DB* (Week 9 table cited **~2,696** on audited snapshot). |

---

## Location signals

- **Posting-level:** `job_postings` location fields (city / state / zip / county text) vs enrichment-derived **`borderplex_subregion`**.
- **`borderplex_subregion` enum (integration contract):** `el_paso` \| `las_cruces` \| `ciudad_juarez` \| `regional` \| `unknown` (see `.cursor/rules/integration-schema.mdc`).

---

## Aggregates — `geo_demand_weekly` vs city-level

- **`geo_demand_weekly`** rolls demand into **Borderplex subregion buckets** only (not full street-level or arbitrary city polygons). For stakeholder questions that imply “El Paso city limits” vs “Las Cruces MSA”, the aggregate may answer at **subregion** grain only.
- **Gap taxonomy:**
  - **Data normalization** — missing or coarse `borderplex_subregion` labels on postings; sparse `geo_demand_weekly` rows for a week.
  - **Prompt / router** — classifier picks `geographic` but router returns `skill_demand_weekly` without geo predicates (see Week 9 trend example in [`week-09-temporal-employer-audit.md`](../../docs/findings/week-09-temporal-employer-audit.md) for “geo terms ignored” class of bug).

---

## Sample gold slice — `gq-041` … `gq-050`

Geographic + comparison Pair C questions in this id band should be re-run with the harness after DB refresh. **Capture row counts** at each evidence step (SQL result `row_count_returned`, intermediate hops if traced) and paste into a future iteration row.

| ID | Intent (golden) | Row-count capture |
|----|-----------------|-------------------|
| gq-041 … gq-050 | *per `qa_golden_questions.json`* | *TBD — fill on scored `--dry-run`* |

---

## Acceptance note

Baseline narrative and checklist for #345; scored re-run artifacts can ship in a follow-up PR if needed (**`Refs #345`** vs **`Fixes #345`** per acceptance).
