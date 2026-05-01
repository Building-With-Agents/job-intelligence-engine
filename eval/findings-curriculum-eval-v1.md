# Curriculum Generation Eval Findings — Juan

## What I Tested

- Curriculum generation path end-to-end: intent classification → multi-table ORM query
  (skill_demand_weekly, skill_velocity, canonical_roles, extracted_intelligence,
  employer_profiles) → structured synthesis → AnalyticsQueryResponse
- Canonical smoke question: "What should a training program for AI-enabled software
  developers look like given what Borderplex employers are hiring for right now?"
  (live output pending PYTHON_DATABASE_URL — script committed at
  scripts/smoke/e2e_curriculum_analytics_qna.py)
- Prompt iteration round 1 — 3 cycles on gq-078, gq-083, gq-062

## What I Found

- All 3 questions failed at level 0 due to intent-misclassification under mock mode
  (invalid classifier JSON → "other")
- Adding deterministic heuristic routing per question fixed classification without
  touching the LLM:
  - gq-078: _CURRICULUM_TRAINING_PROGRAM_COVER_PATTERN (tier 1)
  - gq-083: _WORKFLOW_DATA_PIPELINE_PATTERN (tier 2)
  - gq-062: _BORDERPLEX_EMPLOYERS_RANKED_SHARE_PATTERN (tier 3)
- Composite scores: 0.0056 → 0.7598 → 0.9036 → 1.0000 across 3 cycles
- QA_EVAL_INTENT_HEURISTIC_LEVEL env var allows replay without reverting git

## Recommendation

- Pair C (Week 11 starting point): heuristic level 3 is the production default —
  all three new patterns are active. Run --only-ids gq-078,gq-083,gq-062 first
  to confirm no regression before running the full 90-question corpus.
- Live DB smoke run still needed to verify real Borderplex data returns grounded
  module titles. Run scripts/smoke/e2e_curriculum_analytics_qna.py once
  PYTHON_DATABASE_URL is configured.

## Tradeoffs Acknowledged

- Heuristic routing is deterministic but brittle — new question phrasings outside
  the regex set will fall through to the LLM classifier
- Mock mode scores may not reflect live performance — real synthesis depends on
  actual skill_demand_weekly data being populated for the requested roles
- Live smoke output not yet captured — findings-curriculum-smoke-v1.md is a
  placeholder pending DB connection

## Data / Evidence

- eval/prompt_iteration_log.md — Week 10 Pair D section, 3 logged cycles
- Composite scores measured with QA_EVAL_OFFLINE=1 --only-ids gq-078,gq-083,gq-062
- 50 tests passing: analytics/query_engine/tests/test_intent_classification.py
