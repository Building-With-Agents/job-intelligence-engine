# Week 9 — Temporal & Employer Data Audit (Exercise 9.4)

---

### What I Tested

- **Temporal / aggregates (Fatima):** Distribution of postings by week on `job_postings`; runs of `refresh_aggregates.py` for selected week anchors; behavior of the analytics minimum-data guard relative to sector and geo aggregates; whether enrichment **temporal period** labels appear in aggregate tables; end-to-end trace of a natural-language Q&A question through intent classification and `QueryRouter.route()`.
- **Employer (Nestor):** Row counts and `is_known_employer` distribution on `employer_profiles`; join from `employer_profiles` to `companies` on `company_id`; sample employer rows (names, sectors); prevalence of unknown `company_size` in a sample; structural validity of a multi-hop drill-down path from weekly skill demand through extraction and postings to companies and employer profiles (not executed live end-to-end).

---

### What I Found

- **Posting dates** cluster in recent weeks (Apr 13, Apr 6, Mar 30 dominant; earlier March dates negligible).
- **Aggregate refresh outputs** differ by week: skill/tool demand and skill velocity row counts are large for Apr 13, Apr 6, and Mar 30; **sector and geo weekly rows are zero for Apr 13 and Apr 6**, while Mar 30 shows small non-zero sector/geo counts. **Totals across all weeks:** skill_demand 7862, skill_velocity 7862, sector 2, geo 7.
- **Minimum-data guard:** An **`analytics_minimum_data_guard_no_created_column`** warning fires on every run; the guard references a column that does not exist (mismatch described as **createdat vs created_column**). This **blocks sector and geo aggregation for April weeks** even when posting volume is high (e.g. 2900+ postings in scope for that check).
- **Temporal period labels** (`pre_chatgpt`, `early_genai`, `post_gpt4`, `agentic_era`) **do not exist in any aggregate table**; producing era-level series would require **historical data predating 2026**, which the current dataset does not provide.
- **Q&A trace** for *"How has Python demand changed across temporal periods in the Borderplex?"*: classifier returns **intent `trend`**, confidence **0.9**, entities **Borderplex** + **Python**. **`_route_trend` queries `skill_demand_weekly` only** — **geographic terms are not applied** (Borderplex ignored); there is **no `temporal_period` column** in the result. **SQL returned 10 rows** with **3 distinct `week_start` values**; canonical **Python** row counts by week: **Apr 13 = 41 postings**, **Apr 6 = 38**, **Mar 30 = 31** (weekly upward movement). **Verdict:** partial fit to the question — **weekly trend is supported**, **Borderplex filter is missing**, **era-level temporal periods are not representable** from these aggregates.
- **Employer:** **1373** profiles total; **1370** with `is_known_employer=true` (**99.8%**), **3** unknown. **`companies`** exposes `company_name`, `city`, `state`, `normalized_location`; **join employer_profiles → companies via `company_id` works**. Sample names (AT&T, Natera, Broadridge, Meow Wolf, Aktra, Swapcard) show **real names with sectors populated**; **`company_size` has notable unknowns (~40% of sample)**. The **full drill-down path** (skill_demand_weekly → extracted_intelligence → job_postings → companies → employer_profiles) is **structurally valid** but **not live-tested end-to-end**.

---

### Recommendation

1. **Week 10 — fix the minimum-data guard column reference** so sector/geo aggregation is not blocked when posting thresholds are met (one-line / narrow schema alignment: **createdat vs created_column** as described in the warning).
2. **Week 10 — routing:** If questions combine **trend + geography**, extend **`_route_trend`** (or adjacent routing) so **Borderplex (and other resolved geo terms) constrain or blend** with an appropriate aggregate (e.g. geo demand or a documented join strategy), instead of silently ignoring `geographic_terms`.
3. **Temporal periods in aggregates / Q&A:** Treat **era-level** answers as **out of scope for the current demo** until the pipeline is run on **historical** postings that span those labels; document that **aggregate tables do not carry `temporal_period`** today.
4. **Employer drill-down:** After geo/sector guard behavior is corrected, **run a live end-to-end test** of the documented join path from weekly aggregates to employer profiles.
5. **`company_size` unknowns:** Defer for demo; track as data-quality follow-up if product needs density on size band.

---

### Tradeoffs Acknowledged

- **Fixing the guard first** unblocks sector/geo for high-volume weeks but does not by itself add **temporal_period** to aggregates or Q&A allowlists.
- **Adding geo to `trend`** may duplicate logic with **`_route_geographic`** or require clear product rules (when to use `skill_demand_weekly` vs `geo_demand_weekly` vs multi-step evidence).
- **Historical re-ingestion / backfill** for era labels is a **data and ops** cost, not a router-only change.
- **Employer path live test** waits on stable upstream aggregates and correct guard behavior to avoid conflating join bugs with empty intermediate tables.

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

**Gaps (classified)**  

1. `analytics_minimum_data_guard_no_created_column` — fixable Week 10 (column rename / alignment).  
2. Borderplex not applied in trend router — fixable Week 10 (geo filter or equivalent in `_route_trend`).  
3. Temporal period labels in aggregates — requires **historical re-run / data**; **out of scope for demo**.  
4. `company_size` unknowns — **out of scope for demo**.  
5. Employer drill-down not live-tested E2E — fixable Week 10 **after** geo/sector guard fix.

Owner: Fatima + Nestor (Pair B), Week 9 Exercise 9.4
