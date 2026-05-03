# Curriculum Generation Smoke Test — v1

> **Verified 2026-05-02 with `LLM_PROVIDER=azure_openai` against live Postgres.** This doc supersedes a 2026-04-30 placeholder (run could not execute — `PYTHON_DATABASE_URL` was unset) and a later mock-harness rerun whose output reflected the leakage path now removed in [#358](https://github.com/Building-With-Agents/job-intelligence-engine/pull/358).

Date: 2026-05-02
Command: `python scripts/smoke/e2e_curriculum_analytics_qna.py`
Captured stdout: `C:\Users\garyl\AppData\Local\Temp\curriculum-smoke-postfix.log`

## Question 1

> What should a training program for AI-enabled software developers look like given what Borderplex employers are hiring for right now?

```
Insufficient data: we resolved a target role, but there are no top demanded skills
in the Borderplex aggregates for the current time window, so a skills-grounded
curriculum outline cannot be generated. Check back after the next analytics
refresh, or try a role with more postings in this region.
```

| Metric | Value |
|---|---|
| `confidence` | 0.0 |
| `row_count_returned` | 0 |
| `modules` | `[]` |
| `is_sufficient` | False |

Evidence rows show all four `curriculum_path` queries returned 0 rows except `top_employers` (3 rows). The role resolved correctly but `dbo.skill_demand_weekly` had no top skills for it in the latest week — `data_flags.top_skills=True` short-circuited the LLM synthesis call (cost: $0).

## Question 2

> What should a training program for data analysts look like given what Borderplex employers are hiring for right now?

Same outcome: `confidence=0.0`, `is_sufficient=False`, identical insufficient-data answer body. `top_employers` returned 3 rows, all other curriculum-path queries returned 0.

## Question 3

> What should a training program for cybersecurity analysts look like given what Borderplex employers are hiring for right now?

Same outcome: `confidence=0.0`, `is_sufficient=False`, identical insufficient-data answer body.

## Summary

- Questions run: **3**
- Questions with `is_sufficient = True`: **0**
- Questions with `confidence >= 0.85`: **0**
- Modules with unverified skill names (heuristic vs curriculum inputs): none — synthesis was skipped, no modules emitted.

## Interpretation

The three smoke questions resolved to canonical roles whose `skill_demand_weekly` row count is zero in the latest week (`2026-04-20`). The pipeline correctly emitted the calibrated **insufficient-data** path (no fabrication, no hallucination, `confidence=0.0`).

This is **not** a synthesis or routing regression. It is a real-data finding: those particular role labels are sparse in the post-2026-05-02 re-cluster. By contrast, the demo-set question `gq-072` ("Autonomous AI Agent Engineer") returns a rich 8-module outline because its canonical role *does* have skill rows.

The smoke is therefore **green for calibration** (system honestly reports thin data) and **inconclusive on synthesis quality** (synthesis didn't run for any of the 3). Pair an out-of-band live test against `gq-072` (or any role with rich `skill_demand_weekly` rows) when verifying synthesis-side changes.
