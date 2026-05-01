# Curriculum Generation Eval Findings — Enrique

## What I Tested

- Prompt iteration round 1 — employer/curriculum/workflow question set
- One-change-at-a-time discipline across 3 cycles (gq-078, gq-083, gq-062)
- Mock eval harness with QA_EVAL_OFFLINE=1 and --only-ids

## What I Found

- The dominant failure pattern was intent-misclassification, not synthesis-weak —
  the LLM classifier returned invalid JSON under mock mode, causing all three
  questions to route to "other"
- Deterministic heuristic patterns were more reliable than LLM routing for
  well-defined question shapes
- The one-change discipline revealed that each question had one clear fix —
  no compound changes were needed
- --limit 20 only hits gq-001–gq-020 (disruption/emergence) — use --only-ids
  for employer/curriculum/workflow questions, not --limit

## Recommendation

- For Week 11: Pair C should validate heuristic level 3 against the full 90-question
  corpus before assuming the composite improvement generalizes
- The curriculum synthesis prompt FORBIDDEN block is in place — live validation
  needed to confirm hallucinated skill names are actually prevented with real data
- If new question shapes emerge in Week 11, add heuristic patterns at the
  appropriate tier rather than retraining the LLM classifier

## Tradeoffs Acknowledged

- Offline mock scores are optimistic — real data may surface synthesis-weak failures
  not visible in mock mode
- Heuristic patterns favor precision over recall — unusual phrasings will miss
- Live DB output still pending

## Data / Evidence

- eval/prompt_iteration_log.md — Week 10 Pair D section
- Composite: 0.0056 → 1.0000 over 3 cycles on gq-078, gq-083, gq-062
- Re-run command:

```bash
export LLM_PROVIDER=mock QA_EVAL_OFFLINE=1
QA_EVAL_INTENT_HEURISTIC_LEVEL=3 python -m eval.qa_eval \
  --prompt-version v2-employer-share-ranking-heuristic \
  --only-ids gq-078,gq-083,gq-062 --dry-run
```
