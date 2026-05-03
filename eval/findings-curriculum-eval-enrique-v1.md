# Curriculum Generation Eval Findings — Enrique

> **Verified 2026-05-02 with `LLM_PROVIDER=azure_openai` against live Postgres.** This doc supersedes a prior version whose composite scores reflected the leaky mock harness in PR#351 (now removed in [#358](https://github.com/Building-With-Agents/job-intelligence-engine/pull/358)). Numbers below come from `eval/runs/qa-v2-real-llm-postfix-jie358-2026-05-02.json`.

## What I Tested

- Prompt iteration round 1 — employer / curriculum / workflow question set
- One-change-at-a-time discipline across 3 cycles (`gq-078`, `gq-083`, `gq-062`)
- Pre-iteration baseline: `eval/runs/qa-dev-verify-2026-05-01.json` (real-LLM, May 1)
- Post-iteration: real-LLM run on JIE#358 fix branch (no mock leakage)

## What I Found

- **The dominant claim from the original mock-mode iteration ("intent classifier returned invalid JSON → routed to `other`") is a mock-mode artifact only.** Under real LLM (`chat-gpt41mini`), all three questions classify correctly without the heuristic patches: `intent_accuracy = 1.0` on both May 1 baseline and May 2 post-fix runs.
- **The deterministic heuristic patterns in `analytics/query_engine/intent.py` (`_CURRICULUM_TRAINING_PROGRAM_COVER_PATTERN`, `_WORKFLOW_DATA_PIPELINE_PATTERN`, `_BORDERPLEX_EMPLOYERS_RANKED_SHARE_PATTERN`) are net-zero on intent classification under real LLM** for these three questions. They short-circuit a step the LLM was already getting right; they cost ~$0 inference (regex match) but add maintenance surface.
- **Real composite movement is in `evidence_citation`, driven by PR#351's router refactor**:
  - `gq-078` (curriculum): 0.817 → 0.817 (no change; synthesis skipped, `empty_top_skills`)
  - `gq-083` (workflow): 0.933 → 0.987 (+0.054 — `evidence_citation` 0.733 → 0.947)
  - `gq-062` (employer): 0.800 → 0.855 (+0.055 — `evidence_citation` 0.200 → 0.421)

## Recommendation

- Keep the heuristic patches in `intent.py` as defense-in-depth (cheap insurance against future LLM regressions or mock fall-through), but **do not cite them as a quality lever in stakeholder communication**. The router-side improvements are the actual lever.
- For Week 11 / Pair C: re-run real-LLM eval if the intent classifier model changes (`LLM_DEFAULT`). Heuristic value is conditional on LLM quality.
- The curriculum-synthesis FORBIDDEN block (no invented skills, no out-of-region examples) is still in place and verified in `analytics/query_engine/curriculum_synthesis.py`. Live data validation is implicit in the May 2 smoke (insufficient-data path correctly fired).

## Tradeoffs Acknowledged

- The original mock-mode iteration cycle's "wins" were optical, not real. The ratchet from `0.0056` → `1.0000` reflects the leaky mock returning `must_include` tokens directly. Real-LLM scores were already in the 0.80–0.93 range on May 1.
- Heuristic patterns favor precision over recall; unusual phrasings will still hit the LLM. That's fine — under real LLM, the LLM handles them.
- The `--only-ids` flag in `qa_eval.py` is the surviving mechanical contribution from this iteration cycle and is genuinely useful for targeted cohort runs.

## Data / Evidence

- `eval/prompt_iteration_log.md` — Week 10 Pair D section (real-numbers table)
- `eval/runs/qa-dev-verify-2026-05-01.json` — May 1 real-LLM baseline (per-item scores cited inline above)
- Re-run command (no leakage):

```bash
LLM_PROVIDER=azure_openai python -m eval.qa_eval \
  --prompt-version v2-real-llm-postfix-jie358-2026-05-02 \
  --only-ids gq-078,gq-083,gq-062 --dry-run --json
```
