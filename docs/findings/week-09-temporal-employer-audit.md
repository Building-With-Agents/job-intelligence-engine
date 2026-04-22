# Week 9 — Temporal & Employer Data Audit (Exercise 9.4)

---

### What I Tested

- **Temporal / aggregates (Fatima):** Distribution of postings by week on `job_postings`; runs of `refresh_aggregates.py` for selected week anchors; behavior of the analytics minimum-data guard relative to sector and geo aggregates; whether enrichment **temporal period** labels appear in aggregate tables; end-to-end trace of a natural-language Q&A question through intent classification and `QueryRouter.route()`.
- **Employer (Nestor):** Row counts and `is_known_employer` distribution on `employer_profiles`; join from `employer_profiles` to `companies` on `company_id`; sample employer rows (names, sectors); prevalence of unknown `company_size` in a sample; structural validity of a multi-hop drill-down path from weekly skill demand through extraction and postings to companies and employer profiles (not executed live end-to-end).
- **Employer drill-down audit script (`scripts/employer_drill_down_audit.py`):** Automated five-test audit covering: (1) hop-by-hop row count trace for "data engineer" from `skill_demand_weekly` through `extracted_intelligence`, `job_postings`, and `employer_profiles`; (2) per-hop success rate, orphan count, and fan-out ratio across the full chain; (3) `employer_profiles` name quality check; (4) `week_start`-based join between `skill_demand_weekly` and `sector_summary_weekly` for Python; (5) SEVERITY 1 gap report aggregating all failures.

---

### What I Found

- **Posting dates** cluster in recent weeks (Apr 13, Apr 6, Mar 30 dominant; earlier March dates negligible).
- **Aggregate refresh outputs** differ by week: skill/tool demand and skill velocity row counts are large for Apr 13, Apr 6, and Mar 30; **sector and geo weekly rows are zero for Apr 13 and Apr 6**, while Mar 30 shows small non-zero sector/geo counts. **Totals across all weeks:** skill_demand 7862, skill_velocity 7862, sector 2, geo 7.
- **Minimum-data guard:** An **`analytics_minimum_data_guard_no_created_column`** warning fires on every run; the guard references a column that does not exist (mismatch described as **createdat vs created_column**). This **blocks sector and geo aggregation for April weeks** even when posting volume is high (e.g. 2900+ postings in scope for that check).
- **Temporal period labels** (`pre_chatgpt`, `early_genai`, `post_gpt4`, `agentic_era`) **do not exist in any aggregate table**; producing era-level series would require **historical data predating 2026**, which the current dataset does not provide.
- **Q&A trace** for *"How has Python demand changed across temporal periods in the Borderplex?"*: classifier returns **intent `trend`**, confidence **0.9**, entities **Borderplex** + **Python**. **`_route_trend` queries `skill_demand_weekly` only** — **geographic terms are not applied** (Borderplex ignored); there is **no `temporal_period` column** in the result. **SQL returned 10 rows** with **3 distinct `week_start` values**; canonical **Python** row counts by week: **Apr 13 = 41 postings**, **Apr 6 = 38**, **Mar 30 = 31** (weekly upward movement). **Verdict:** partial fit to the question — **weekly trend is supported**, **Borderplex filter is missing**, **era-level temporal periods are not representable** from these aggregates.
- **Employer:** **1373** profiles total; **1370** with `is_known_employer=true` (**99.8%**), **3** unknown. **`companies`** exposes `company_name`, `city`, `state`, `normalized_location`; **join employer_profiles → companies via `company_id` works**. Sample names (AT&T, Natera, Broadridge, Meow Wolf, Aktra, Swapcard) show **real names with sectors populated**; **`company_size` has notable unknowns (~40% of sample)**. The **full drill-down path** (skill_demand_weekly → extracted_intelligence → job_postings → companies → employer_profiles) is **structurally valid** but **not live-tested end-to-end**.
- **Employer drill-down audit (live script run, 2026-04-22):** The automated audit confirmed and quantified the structural gap. `job_postings.employer_profile_id` is **NULL on all 2,696 rows** — the FK was never written because fixture-seeded rows bypassed `job_postings_promotion.py`. As a result, the canonical drill-down join (`job_postings → employer_profiles` via `employer_profile_id`) has a **0% success rate** and **100% orphan rate**. The `employer_profiles` table itself is clean: **1,378 distinct profiles**, **0% unknown name rate**. The hop `extracted_intelligence → normalized_jobs` is **100% clean** (3,141 / 3,141). The hop `normalized_jobs → job_postings` has a **42% orphan rate** (1,317 of 3,139 `normalized_jobs` rows have no matching `job_postings` entry — normalized and extracted but never promoted). The `sector_summary_weekly` join for Python returned **1 row** (sector: "Other", 5 postings) — technically functional but not meaningful because NAICS classification is incomplete and most postings fall into the "Other" bucket.

---

### Recommendation

1. **Week 10 — fix the minimum-data guard column reference** so sector/geo aggregation is not blocked when posting thresholds are met (one-line / narrow schema alignment: **createdat vs created_column** as described in the warning).
2. **Week 10 — routing:** If questions combine **trend + geography**, extend **`_route_trend`** (or adjacent routing) so **Borderplex (and other resolved geo terms) constrain or blend** with an appropriate aggregate (e.g. geo demand or a documented join strategy), instead of silently ignoring `geographic_terms`.
3. **Temporal periods in aggregates / Q&A:** Treat **era-level** answers as **out of scope for the current demo** until the pipeline is run on **historical** postings that span those labels; document that **aggregate tables do not carry `temporal_period`** today.
4. **Employer drill-down — `employer_profile_id` backfill:** Run the one-off SQL fix to populate the NULL FK on all existing `job_postings` rows by matching `job_postings.company_id` to `employer_profiles.company_id`. This is free (no LLM calls), deterministic, and unblocks the canonical drill-down join. After the fix, re-run `scripts/employer_drill_down_audit.py` to confirm 0 SEVERITY 1 gaps remain.
5. **Employer drill-down — 1,317 orphaned `normalized_jobs`:** Investigate why 42% of `normalized_jobs` rows have no matching `job_postings` entry. Likely cause: the `source` / `external_id` composite key does not match between the two tables for those rows. These records are stuck mid-pipeline — normalized and extracted but never promoted.
6. **Sector connection — NAICS classification backfill:** Run `scripts/backfill_enrichment.py` to classify NAICS codes on unclassified `job_postings` rows, then re-run `refresh_aggregates.py`. This will produce a meaningful sector distribution beyond the current "Other" catch-all.
7. **`company_size` unknowns:** Defer for demo; track as data-quality follow-up if product needs density on size band.

---

### Tradeoffs Acknowledged

- **Fixing the guard first** unblocks sector/geo for high-volume weeks but does not by itself add **temporal_period** to aggregates or Q&A allowlists.
- **Adding geo to `trend`** may duplicate logic with **`_route_geographic`** or require clear product rules (when to use `skill_demand_weekly` vs `geo_demand_weekly` vs multi-step evidence).
- **Historical re-ingestion / backfill** for era labels is a **data and ops** cost, not a router-only change.
- **Employer path live test** waits on stable upstream aggregates and correct guard behavior to avoid conflating join bugs with empty intermediate tables.
- **`employer_profile_id` backfill vs full pipeline re-run:** The SQL backfill is the lowest-cost fix, but it will only link the ~1,379 `job_postings` rows whose `company_id` already has a matching `employer_profiles` entry. The 1,317 orphaned `normalized_jobs` rows (never promoted to `job_postings`) require a full pipeline re-run — which is costly and may re-trigger LLM extraction charges.
- **NAICS classification depth:** Backfilling NAICS will improve sector aggregation but introduces LLM cost per unclassified record. Unless a batch budget is established first, do not run against the full 2,696 `job_postings` corpus.
- **Sector join quality vs join existence:** The `skill_demand_weekly ↔ sector_summary_weekly` join on `week_start` now works, but a technically passing join returning one "Other" row is not meaningful to a stakeholder Q&A. Fixing the NAICS classification first is a prerequisite for the sector Q&A to be demo-ready.

---

### Data / Evidence

**Temporal — `job_postings` date distribution (counts by anchor date)**  
Apr 13 = 1622, Apr 6 = 1309, Mar 30 = 556, Mar 23 = 6, Mar 16 = 1, Mar 9 = 2.

**Temporal — `refresh_aggregates.py` (by `week_start`)**  

| week_start | skill_demand | tool_demand | skill_velocity | sector | geo |
|------------|-------------|-------------|------------------|--------|-----|
| 2026-04-13 | 3406 | 70 | 3406 | 0 | 0 |
| 2026-04-06 | 2229 | 76 | 2229 | 0 | 0 |
| 2026-03-30 | 2227 | 81 | 2227 | 1 | 2 |

**Temporal — totals across all weeks**  
skill_demand = 7862, skill_velocity = 7862, sector = 2, geo = 7.

**Temporal — guard / schema**  
`analytics_minimum_data_guard_no_created_column` on every run; root cause: minimum-data guard references a **non-existent column** (**createdat vs created_column**); effect: **blocks sector and geo for April weeks** despite **2900+ postings** in the relevant guard context.

**Temporal — aggregate schema vs enrichment**  
Temporal period labels (`pre_chatgpt`, `early_genai`, `post_gpt4`, `agentic_era`) **do not exist in any aggregate table**; meaningful era breakdown needs **historical data predating 2026**.

**Q&A trace — question**  
*"How has Python demand changed across temporal periods in the Borderplex?"*

**Q&A trace — classification**  
Intent: **trend**, confidence **0.9**, extracted: `geographic_terms=[Borderplex]`, `skill_names=[Python]`.

**Q&A trace — router**  
`_route_trend` → **`skill_demand_weekly` only**; **geo terms not applied**; **no `temporal_period` column**.

**Q&A trace — query outcome**  
**10 rows**, **3 distinct `week_start`**; Python row: **Apr 13 = 41**, **Apr 6 = 38**, **Mar 30 = 31** postings. **Verdict:** partial — weekly trend supported; Borderplex filter missing; era-level periods not possible from current aggregates.

**Employer — `employer_profiles`**  
1373 total; 1370 `is_known_employer=true` (99.8%); 3 unknown.

**Employer — `companies` and join**  
Columns include `company_name`, `city`, `state`, `normalized_location`; **join employer_profiles → companies via `company_id` works**.

**Employer — sample**  
AT&T, Natera, Broadridge, Meow Wolf, Aktra, Swapcard — real names, sectors populated; **~40% of sample** with unknown **`company_size`**.

**Employer — drill-down path**  
skill_demand_weekly → extracted_intelligence → job_postings → companies → employer_profiles: **structurally valid**; **not live-tested end-to-end**.

**Employer drill-down audit — hop-by-hop row counts (data engineer, 2026-04-22)**

| Hop | Table | Rows | Drop vs. previous |
|-----|-------|------|-------------------|
| 0 | `skill_demand_weekly` (data engineer) | 1 | — |
| 1 | `extracted_intelligence` (JSONB skill match) | 176 | — |
| 2 | `job_postings` (via `normalized_job_id`) | 98 | -44% |
| 3 | `employer_profiles` (via `employer_profile_id`) | 0 | -100% |

**Employer drill-down audit — hop integrity metrics**

| Hop | Success Rate | Orphan Count | Fan-out Ratio |
|-----|-------------|-------------|---------------|
| `normalized_jobs → job_postings` | 58% | 1,317 | 0.84 |
| `job_postings → employer_profiles` | 0% | 2,696 | 0.00 |
| `extracted_intelligence → normalized_jobs` | 100% | 0 | 1.01 |

**Employer drill-down audit — profile health**  
1,378 distinct profiles; 0 null/empty/Unknown names; **0.0% unknown rate**.

**Employer drill-down audit — sector connection (Python, week_start join)**  
`skill_demand_weekly` rows: 1,657; `sector_summary_weekly` rows: 4; join result: **1 row** (sector: "Other", skill_count: 5, sector_count: 273).

**Employer drill-down audit — SEVERITY 1 gaps (script output)**  
1. `employer_profiles` reached 0 rows — `job_postings.employer_profile_id` is NULL on all rows; FK never written by enrichment promotion step.  
2. `job_postings → employer_profiles` join: 0% success rate, 100% orphan rate (2,696 orphaned rows).  
3. Sector connection: 1 row returned — incomplete NAICS classification buckets most postings into "Other".

**Gaps (classified)**  

1. `analytics_minimum_data_guard_no_created_column` — fixable Week 10 (column rename / alignment).  
2. Borderplex not applied in trend router — fixable Week 10 (geo filter or equivalent in `_route_trend`).  
3. Temporal period labels in aggregates — requires **historical re-run / data**; **out of scope for demo**.  
4. `company_size` unknowns — **out of scope for demo**.  
5. Employer drill-down not live-tested E2E — fixable Week 10 **after** geo/sector guard fix.
6. `job_postings.employer_profile_id` NULL (all 2,696 rows) — **SEVERITY 1**; root cause: fixture-seeded rows bypassed `job_postings_promotion.py`; fix: one-off SQL backfill matching `company_id`.  
7. 1,317 `normalized_jobs` orphans (no matching `job_postings`) — **SEVERITY 1**; root cause: `source`/`external_id` mismatch or records stuck in extract-but-not-promote state.  
8. Sector Q&A returning only "Other" — **SEVERITY 1** (for demo purposes); root cause: NAICS classification incomplete; fix: `backfill_enrichment.py` then `refresh_aggregates.py`.

Owner: Fatima + Nestor (Pair B), Week 9 Exercise 9.4
