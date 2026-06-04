# ADR-009 — Week 10 Eval Scoring Strategy

| | |
|---|---|
| **Owner** | Gary (lead instructor); Pair C (implementation) |
| **Experiment** | #264 (Langfuse LLM-as-a-Judge side-by-side comparison) |
| **Status** | **Accepted** — Option D (Hybrid): custom scorer fixed + Langfuse judge enabled |
| **Decided** | Week 11 (all sub-issues closed; empirical evidence gathered in #264) |
| **Original date** | Week 9 → Week 10 |

## Context

The Week 9 Q&A eval harness ships a custom deterministic scorer at `eval/qa_scoring.py` producing five per-item metrics (`intent_accuracy`, `evidence_citation`, `confidence_flags`, `latency_sla`, `answerability`) plus a weighted composite. Code review during 2026-04-24 surfaced significant design debt:

- Refusal path bypasses rubric checks and floors 80% of intent-only items at 0.70 (#260)
- `intent_accuracy` uses a three-valued ladder backed by an arbitrary graph, smuggling ambiguity into a discrete task (#261)
- Short-circuits conflate infrastructure failures with quality failures (#263)
- No per-class, per-dimension, or per-item error decomposition — unlike the extraction eval Fabian and Angel built (#265)
- Evidence-citation grounding is lexical overlap, not semantic — cannot reward paraphrase or implicit reasoning

Week 10 prompt iteration depends on metrics that respond sensitively to prompt changes. The current scorer does not meet that bar. This ADR addresses the architectural question: **what scoring approach should the Q&A eval harness use for Weeks 10 and beyond?**

## Decision

**Proposed (pending #264):** Run Langfuse's native LLM-as-a-Judge in parallel with the fixed custom scorer during Week 10. Use the side-by-side comparison data to make a definitive choice at the Week 11 boundary. Do not adopt RAGAs wholesale until empirical evidence justifies the dependency cost.

## Options Considered

### Option A — Keep and fix the custom scorer

Continue improving `eval/qa_scoring.py` via the already-filed issues (#260, #261, #263, #265, plus #262 per-class F1). Resolve the architectural issues in place; do not adopt external libraries.

### Option B — Adopt RAGAs wholesale

Replace `evidence_citation` (and possibly `answerability`) with [RAGAs](https://docs.ragas.io/) metrics (`faithfulness`, `answer_relevance`, `context_recall`). Keep `intent_accuracy`, `confidence_flags`, `latency_sla` as custom — RAGAs does not have strong equivalents for these.

### Option C — Use Langfuse native LLM-as-a-Judge

Configure one or two LLM-as-a-Judge rubrics in Langfuse directly. The judge reads traces from the existing dataset and emits scores via the Langfuse Scores API — no new Python dependencies, no new CI surface, uses existing Azure OpenAI keys. Keep the custom scorer for the non-semantic metrics (`intent_accuracy`, `confidence_flags`, `latency_sla`, `answerability`).

### Option D — Hybrid: fix custom + add Langfuse judge in parallel

Land the fixes from #260, #261, #263, #265 to sharpen the custom scorer. Simultaneously enable Langfuse LLM-as-a-Judge per Option C. Compare them empirically against human Layer 2 scoring during Week 10. At the Week 11 boundary, pick a winner or consolidate.

## Tradeoff Matrix

| Dimension | A: Fix custom | B: Adopt RAGAs | C: Langfuse judge | D: Hybrid |
|---|---|---|---|---|
| **Signal quality** | Lexical only — cannot reward paraphrase or implicit reasoning | Semantic via LLM judge — handles paraphrase, hallucination detection | Semantic via LLM judge — same capability as RAGAs | Best of both — lexical audit + semantic judgment |
| **Cost per run** | ~$0 (no LLM calls by scorer) | ~$1–3 per 90-item run (3 metrics × 90 items) | ~$0.50–2 per 90-item run (1–2 judges × 90 items) | ~$0.50–2 (just the judge) |
| **Dependency weight** | None new | Langchain, embeddings SDK, RAGAs — significant new deps | None — uses existing Langfuse SDK | None new (judge is UI config) |
| **Determinism** | Deterministic (same input → same score) | Probabilistic; RAGAs averages multiple samples | Probabilistic; depends on temperature setting | Lexical side deterministic; judge side probabilistic |
| **Maintenance burden** | High — we own every line | Low — library maintains the scorer | Low — config-only, no Python |  Medium — we own the lexical side |
| **Interpretability** | High — algorithm is inspectable | Medium — LLM reasoning is visible but not reducible | High — judge rationale attaches to each trace in the UI | High — both views available |
| **Refusal handling** | Bad today (#260) — fix needed | RAGAs faithfulness handles gracefully | Can be explicitly rubric-prompted | Fix lexical side + judge cross-checks |
| **Speed** | Fast (milliseconds) | Slow (seconds per item; RAGAs calls LLM multiple times) | Slow (seconds per item) | Fast scorer + slow judge; run judge async or overnight |
| **Week 10 readiness** | Needs #260, #261, #263, #265 landed first | Needs prototype + env setup + test coverage | UI-only configuration, ships in one afternoon | Needs #260/#261/#263 landed + judge configured |
| **Eval-on-eval recursion risk** | None (deterministic) | High if judge = same model family as generator | Configurable — can pick a stronger judge model (`chat-gpt41` judging `chat-gpt41mini` synthesis) | Same as C |
| **v1-baseline continuity** | Partial — fixes change the numeric interpretation | Broken — new metrics incomparable without re-running | Preserved — judge is additive, existing scores untouched | Preserved (judge additive) with fixed lexical scorer drifting from v1 |

## Recommendation

**Option D (Hybrid) for Week 10, revisit at Week 11.**

Reasoning:

1. **The custom-scorer fixes are tractable and high-leverage regardless of what else we do.** #260, #261, #263, #265 are good changes whether or not we ultimately adopt an LLM judge. Land them.
2. **Langfuse LLM-as-a-Judge is the cheapest possible way to gather decision-relevant evidence.** UI configuration only; uses existing keys; marginal LLM cost is $0.50–2 per run. One afternoon to stand up.
3. **Running both in parallel for Week 10 generates the empirical evidence that justifies (or doesn't) a later migration to RAGAs.** Week 10 will score 3–5 prompt versions × 90 items = 270–450 human-rated data points. Comparing judge scores vs. custom scores vs. human scores on that data is the definitive input to a Week 11 decision.
4. **Delaying the RAGAs adoption avoids premature commitment.** If the Langfuse judge meets the bar, we never pay RAGAs's dependency cost. If it doesn't, we have the data to justify RAGAs's trade-offs.

## What We Will Test (#264 experiment)

### Sub-hypothesis 1: Judge correlates better with human Layer 2 than the custom scorer

**Measurement:** Spearman rank correlation of `judge_faithfulness` vs. `human_correctness` across the 90 v1-baseline items. Same for custom `evidence_citation` vs. `human_correctness`.

**Pass bar:** Judge correlation > custom correlation by at least 0.10 at p < 0.05.

### Sub-hypothesis 2: Judge differentiates cohorts the custom scorer cannot

**Measurement:** For data-backed vs. intent-only items, does the judge score shift more than the custom scorer? For wrongful refusals vs. correct refusals, same question.

**Pass bar:** Judge produces per-cohort means that differ by > 0.15; custom scorer's differ by < 0.10.

### Sub-hypothesis 3: Judge cost is within acceptable bounds for weekly iteration

**Measurement:** Total USD spend on judge calls for a 90-item run.

**Pass bar:** < $5 per run. At 5 runs during Week 10 that is < $25 — acceptable.

### Sub-hypothesis 4: Judge variance is small enough to trust

**Measurement:** Run the same judge against the same 90 items twice. Compute per-item score delta distribution.

**Pass bar:** 95th percentile per-item delta < 0.1 on a 0.0–1.0 scale.

## Tradeoffs Acknowledged

- **Running both scorers doubles the cognitive load during Week 10** for pairs reading the scores page. Mitigation: clearly label which scores are custom vs. judge; document in the walkthrough doc.
- **The judge is not fully deterministic.** Even at temperature=0, LLM outputs carry small stochastic variance. Mitigation: measure variance in sub-hypothesis 4; re-run at Week 11 boundary to confirm stability.
- **If the judge becomes the primary scorer, LLM availability becomes a hard dependency.** Azure OpenAI outages would block eval runs. Mitigation: keep the custom scorer as the offline fallback path; document the fallback in `eval/README.md`.
- **Judge rubric authorship is a new skill.** Prompt-engineering for judges is subtly different from prompt-engineering for the pipeline. Mitigation: Pair C owns initial rubric drafts; cross-pair review in Week 10.
- **The Langfuse native judge is less battle-tested than RAGAs.** It is newer, less documented, used by fewer teams. Mitigation: if the Langfuse judge is insufficient at Week 11, revisit RAGAs with the comparison data already in hand — we will not have wasted the exercise.

## Decision outcome (Week 11)

**Option D accepted.** Evidence collected during Week 10 via issues #260–#271 and #264:

- [x] Custom scorer fixed: #260 (refusal redesign), #261 (binary intent), #263 (None-for-infra),
      #265 (component scores), #267 (confidence redesign), #268 (sub-composites), #269
      (answerability + correct_refusal), #270 (latency tail aggregates), #271 (Layer 2 ECE)
- [x] Langfuse LLM-as-a-Judge enabled per #264 (Gary); side-by-side data gathered on v1-baseline
- [x] Human Layer 2 scores (`correctness`, `decision_relevance`, `followup_quality`) scored in
      Langfuse UI; IRR report generated via `scripts/compute_layer2_irr.py`
- [x] Divergence report `eval/qa_divergence_report.md` produced by
      `scripts/compute_automated_human_divergence.py`; high-auto-low-human items prioritised for
      Week 11 prompt iteration
- [x] Cost within bounds: judge cost confirmed acceptable per #264

**No migration to RAGAs required.** The fixed custom scorer + Langfuse LLM-as-a-Judge meets the
Week 10–11 iteration bar. RAGAs remains an option if the judge proves insufficient at Week 12 or
beyond.

**Custom scorer is the offline fallback.** When Langfuse / Azure OpenAI is unavailable, the
deterministic custom scorer (`eval/qa_scoring.py`) runs without LLM calls and produces
reproducible results.

## Data / Evidence (collected)

- Custom scorer scores with all fixes applied: `eval/runs/qa-v2-scorer-redesign.json`
- v1-baseline re-scored under v2 semantics: `eval/runs/qa-v1-baseline.json`
- IRR report: `eval/qa_irr_report.md` (generated post Week 10 Layer 2 annotation)
- Divergence report: `eval/qa_divergence_report.md`
- Langfuse dataset runs: `qa-golden-v1-baseline`, `qa-golden-v2-scorer-redesign` on
  `langfuse.watechcoalition.org`

## Adjacent and Prior Work

- ADR-006 — Observability & Tracing (established Langfuse as the trace platform)
- `eval/extraction_eval_core.py` — Fabian & Angel's extraction eval, the reference implementation for per-dimension P/R/F1 rigor
- IMP-030 (referenced in `eval/qa_scoring.py:161`) — placeholder for "LLM judge upgrade" work
- Issues #260, #261, #262, #263, #264, #265 — the custom-scorer fix track that runs in parallel with this ADR's experiment
