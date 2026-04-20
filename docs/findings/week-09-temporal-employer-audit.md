# Week 9 — Temporal Comparison + Employer Drill-Down Audit
**Owner:** Fatima (temporal) + Nestor (employer)  
**Date:** 2026-04-20  
**Branch:** week-09/temporal-employer-audit

## What I Tested
- Row counts and date coverage in `skill_demand_weekly` and `skill_velocity`
- Whether temporal period labels exist on aggregate tables
- `skill_velocity` week-over-week calculations
- Test question: "How has Python demand changed across temporal periods in the Borderplex?"
- `job_postings` date range vs aggregate week coverage

## What I Found

### Gap 1 — Only one week of aggregate data (CRITICAL)
**Table:** `skill_demand_weekly`, `skill_velocity`  
**Finding:** Both tables contain data for only one week: `2026-04-13`. There are 3,406 skill rows but all from a single snapshot.  
**Root cause:** `refresh_aggregates.py` defaulted to the current week (2026-04-13), but `job_postings` only has data from 2026-03-13 to 2026-04-03. The aggregate ran for a week with no postings.  
**Effect on Q&A:** Any temporal comparison question ("How has Python demand changed over time?") will return a single data point. Trend answers will be meaningless.  
**Classification:** Fixable in Week 10 — re-run `refresh_aggregates.py --week` for each week in the 2026-03-13 to 2026-04-03 range.

### Gap 2 — No temporal period labels on aggregate tables
**Table:** `skill_demand_weekly`  
**Finding:** No `temporal_period` column exists (pre_chatgpt, early_genai, post_gpt4, agentic_era). The table only has `week_start` as a date.  
**Effect on Q&A:** Intent router cannot filter by temporal era. Questions like "How did Python demand change post-GPT4?" have no column to filter on.  
**Classification:** Requires pipeline re-run + schema addition. Medium effort.

### Gap 3 — skill_velocity week-over-week calculations are all 0.0
**Table:** `skill_velocity`  
**Finding:** All 3,406 rows have `week_over_week_change = 0.0` and `four_week_trend = 'emerging'` with `trend_confidence = 0.8`. This is the default fallback — no real calculation possible with one week of data.  
**Effect on Q&A:** Velocity/trend questions will return misleading "emerging" for every skill.  
**Classification:** Automatically fixed once Gap 1 is resolved (multiple weeks populated).

### Gap 4 — sector_summary_weekly and geo_demand_weekly are empty
**Table:** `sector_summary_weekly` (0 rows), `geo_demand_weekly` (0 rows)  
**Finding:** Steps 6 and 7 of the pipeline ran but produced 0 rows.  
**Effect on Q&A:** Geographic and sector questions will return no data.  
**Classification:** Requires pipeline investigation — likely same root cause as Gap 1.

## Test Question Results

**"How has Python demand changed across temporal periods in the Borderplex?"**  
- Python found in `skill_demand_weekly`: 41 postings, 5 employers — but only for week 2026-04-13  
- No prior weeks to compare against  
- Answer: **Cannot be answered. Single week snapshot only.**

## Top 3 Gaps by Demo Impact
1. **Gap 1** — Only one week of data. No temporal comparison is possible at all. Fix: backfill weekly aggregates for March 13 – April 3 range.
2. **Gap 4** — geo_demand_weekly empty. Geographic questions (Pair C's golden questions) will all fail.
3. **Gap 2** — No temporal period labels. Era-based comparisons blocked even after backfill.

## Recommendation for Week 10
Run `refresh_aggregates.py` for each Monday in the job_postings date range:
```bash
python scripts/smoke/refresh_aggregates.py --week 2026-03-16
python scripts/smoke/refresh_aggregates.py --week 2026-03-23
python scripts/smoke/refresh_aggregates.py --week 2026-03-30
```
This will populate 3 additional weeks and enable real week-over-week velocity calculations.

## Tradeoffs Acknowledged
- Not fixing temporal period labels this week — schema change requires migration and pipeline update, out of scope for audit timebox.
- Not investigating geo/sector empty tables beyond root cause hypothesis — Nestor's employer audit may shed more light on join path issues.

## Data Evidence
| Table | Rows | Weeks | Date Range |
|-------|------|-------|------------|
| skill_demand_weekly | 3,406 | 1 | 2026-04-13 only |
| skill_velocity | 3,406 | 1 | 2026-04-13 only |
| sector_summary_weekly | 0 | 0 | — |
| geo_demand_weekly | 0 | 0 | — |
| job_postings (upstream) | 3,496 | ~4 | 2026-03-13 to 2026-04-03 |
