# Curriculum Generation Eval Findings — Juan

> **Verified 2026-05-02 with `LLM_PROVIDER=azure_openai` against live Postgres.** This doc supersedes a prior version whose composite scores reflected the leaky mock harness in PR#351 (now removed in [#358](https://github.com/Building-With-Agents/job-intelligence-engine/pull/358)). Numbers below come from `eval/runs/qa-v2-real-llm-postfix-jie358-2026-05-02.json` and `scripts/smoke/e2e_curriculum_analytics_qna.py`.

## What I Tested

- Curriculum generation path end-to-end: intent classification → multi-table ORM query (`skill_demand_weekly`, `skill_velocity`, `canonical_roles`, `extracted_intelligence`, `employer_profiles`) → structured synthesis → `AnalyticsQueryResponse`
- Live smoke (3 questions) via `scripts/smoke/e2e_curriculum_analytics_qna.py` against the live DB
- Targeted eval pass on `gq-078` (curriculum), `gq-083` (workflow), `gq-062` (employer) under real LLM

## What I Found

- **Intent classification was already at `intent_accuracy = 1.0`** on May 1 (pre-PR#351 baseline) for all three questions. The new heuristic patterns shipped in PR#351 (`_CURRICULUM_TRAINING_PROGRAM_COVER_PATTERN`, `_WORKFLOW_DATA_PIPELINE_PATTERN`, `_BORDERPLEX_EMPLOYERS_RANKED_SHARE_PATTERN`) are net-zero on intent under real LLM — they short-circuit a step the LLM was already getting right.
- **Evidence-citation lift on `gq-083` and `gq-062` is real but modest.** Composite improvement comes from PR#351's router-side changes (`_ilike_company_location_or` routing geo to company HQ; relaxed company-name hint heuristic), not from the heuristic short-circuits. Per-item deltas:
  - gq-078 (curriculum): composite 0.817 → 0.817 (no change; synthesis skipped due to `empty_top_skills`)
  - gq-083 (workflow): composite 0.933 → 0.987 (+0.054, `evidence_citation` 0.733 → 0.947)
  - gq-062 (employer): composite 0.800 → 0.855 (+0.055, `evidence_citation` 0.200 → 0.421, `must_include_recall` 0.494 → 0.639)
- **Live curriculum smoke (3 questions): all 3 returned "Insufficient data — no top demanded skills" under real DB.** The roles those smoke questions resolved to ("AI-enabled software developers", "data analysts", "cybersecurity analysts") have zero rows in `dbo.skill_demand_weekly` for the latest week. By contrast, `gq-072` (Autonomous AI Agent Engineer) does have skill rows and returns a rich 8-module outline. Sparse-data per role, not a prompt issue.

## Recommendation

- The `QA_EVAL_INTENT_HEURISTIC_LEVEL` env var was tied to the now-removed `QA_EVAL_OFFLINE` mock; it has no effect under real LLM. Prod default is whatever ships in `analytics/query_engine/intent.py`.
- Curriculum synthesis is data-density-bound. Roles with thin `skill_demand_weekly` rows produce "Insufficient data" — that's the **calibrated** behavior, not a regression. Pair this with `gq-072` (rich data → high confidence) when demoing for narrative contrast.
- Run `--only-ids gq-078,gq-083,gq-062` periodically as a cheap regression-check on the three iteration questions; full 90-corpus eval takes longer and isn't required for this smoke.

## Tradeoffs Acknowledged

- Heuristic short-circuits in `intent.py` add maintenance surface (regex patterns) without measurable real-LLM benefit on these three questions. They may pay off on other questions or when `chat-gpt41mini` quality drops; not currently load-bearing.
- Curriculum smoke output reflects production data-density, which the eval rubric does not penalize separately. A clean "insufficient data" answer scores low on `answerability` (0.0 for gq-078), but that's correct calibration.

## Data / Evidence

- `eval/prompt_iteration_log.md` — Week 10 Pair D section (rewritten with real numbers, same date as this doc)
- `eval/runs/qa-dev-verify-2026-05-01.json` — pre-PR#351 baseline (cited inline above)
- Re-run command: `python -m eval.qa_eval --prompt-version v2-real-llm-postfix-jie358-2026-05-02 --only-ids gq-078,gq-083,gq-062 --dry-run --json`
- Smoke: `python scripts/smoke/e2e_curriculum_analytics_qna.py` (3/3 returned "Insufficient data" on live data 2026-05-02)
- 123 tests passing in `analytics/tests/test_router.py` + `analytics/query_engine/tests/test_intent_classification.py`
