# Employer drill-down — Ask the Data Q&A baseline (#344)

## Snapshot header

| Field | Value |
|-------|--------|
| **Date** | 2026-05-04 |
| **Branch / SHA** | `fix/week10-eval-baseline-340-344-345` (see git at commit time) |
| **DB provenance** | Local / SoT PostgreSQL via `PYTHON_DATABASE_URL` (not bundled with repo). |
| **`job_postings` approx. count** | *Populate from* `SELECT COUNT(*) FROM dbo.job_postings` *on your DB* (Week 9 audit cited **~2,696** postings on the audited snapshot; see [`week-09-temporal-employer-audit.md`](../../docs/findings/week-09-temporal-employer-audit.md)). |

---

## Q&A path

- **Default harness path:** in-process `run_analytics_qna` (`analytics.query_engine.routing`).
- **Optional:** `python -m eval.qa_eval --use-http --analytics-base-url …` for wire-level parity with deployed `POST /analytics/query`.

---

## SQL guardrails — `validate_ask_the_data_sql`

NL-generated SQL is validated in **`analytics/query_engine/sql_guardrails.py`** via **`validate_ask_the_data_sql(sql) -> (ok, reason, normalized_sql)`** (SELECT-only, allowed tables, row cap, timeout semantics per tests in `analytics/query_engine/tests/test_ask_the_data_guardrails.py`).

**Allowlist (employer drill-down relevant):**

- **`dbo.employer_profiles`** — employer attributes keyed for stakeholder drill-down.
- **`dbo.skill_demand_weekly`** — weekly skill demand; carries **`employer_count`** (IMP-021 / distinct employers in the bucket; see `common.data_store.models.SkillDemandWeekly` and `.cursor/rules/skill-tool-demand.mdc`).

Hop-by-hop row counts and orphan analysis for the “data engineer” style path are automated in **`scripts/employer_drill_down_audit.py`**. On Windows, prefer:

```powershell
$env:PYTHONIOENCODING = "utf-8"
python scripts/employer_drill_down_audit.py
```

---

## Evidence chain (conceptual)

1. **`skill_demand_weekly`** — filter by `skill_label` / `week_start`; read `posting_count`, **`employer_count`**.
2. **`extracted_intelligence`** / **`normalized_jobs`** / **`job_postings`** — promotion chain for posting-level evidence.
3. **`companies`** — name / geo / sector joins.
4. **`employer_profiles`** — `company_id` join; `is_known_employer`, sector, size band.

Week 9 audit (**#289**-class data gaps if `employer_profile_id` null on postings) is summarized in [`docs/findings/week-09-temporal-employer-audit.md`](../../docs/findings/week-09-temporal-employer-audit.md): structural validity of the join graph vs fixture-era NULL FKs and orphaned `normalized_jobs`.

---

## Risks / follow-ups

- **Orphans:** `normalized_jobs` without `job_postings` promotion inflate “missing evidence” in Q&A traces; distinguish from SQL refusal.
- **Employer profile FK:** until `job_postings.employer_profile_id` is populated, drill-down from postings → `employer_profiles` may be empty even when profiles exist (**#289**).
- **Sector join quality:** `skill_demand_weekly` ↔ `sector_summary_weekly` may return sparse “Other” buckets until NAICS backfill (Week 9 doc §6).

---

## Acceptance note

This file satisfies the **documentation** checklist for #344; remaining gaps (live row counts, post-fix re-audit) are tracked in the PR body if using **`Refs #344`**.
