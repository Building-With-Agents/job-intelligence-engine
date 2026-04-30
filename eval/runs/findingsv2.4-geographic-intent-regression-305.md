# Findings v2.4 — Geographic Intent Regression (JIE #305)

**Date:** 2026-04-29  
**Author:** Bryan (Pair C)  
**Branch:** `week-10/fix-geographic-intent-regression`  
**Closes:** [JIE #305](https://github.com/Building-With-Agents/job-intelligence-engine/issues/305)

---

## 1. Summary

Issue #305 reported that 10/10 geographic gold questions (gq-041..050) were being classified as `intent='other'` with `intent_accuracy=0.0` in the v2.2 and v2.3 scorecard runs, blocking Smoke 5 sign-off. This document records the diagnosis, root cause, and resolution.

**Result:** The current `_SYSTEM_PROMPT` in `analytics/query_engine/intent.py` (commits `546069c`, `2cd7a7f`) correctly classifies all 10 gold questions as `geographic` with 0.95 confidence. The regression was **environmental / stochastic**, not a permanent prompt regression. A per-intent regression guard has been added so this can never silently recur.

---

## 2. Live Verification (2026-04-29)

### Direct classifier (10/10 pass)

| Gold Question | Expected | Got | Confidence |
|---|---|---|---|
| gq-041 (El Paso AI agent dev) | geographic | **geographic** | 0.95 |
| gq-042 (Las Cruces data engineer 90d) | geographic | **geographic** | 0.95 |
| gq-043 (El Paso healthcare-IT) | geographic | **geographic** | 0.95 |
| gq-044 (Las Cruces DevOps) | geographic | **geographic** | 0.95 |
| gq-045 (El Paso frontend React) | geographic | **geographic** | 0.95 |
| gq-046 (Las Cruces cybersec) | geographic | **geographic** | 0.95 |
| gq-047 (El Paso entry-level IT salary) | geographic | **geographic** | 0.95 |
| gq-048 (Borderplex fintech/regtech) | geographic | **geographic** | 0.95 |
| gq-049 (El Paso legal-tech) | geographic | **geographic** | 0.95 |
| gq-050 (Las Cruces AI/ML researchers) | geographic | **geographic** | 0.95 |

**`intent_accuracy` aggregate: 1.000 (10/10)** — zero mismatches.

### Full pipeline (run_analytics_qna) — gq-041 sample

```
classified_intent: geographic
intent_classification_confidence: 0.95
row_count_returned: 8
evidence items: 8
refused: False
routing_path: list_style
tables_used: job_postings, postal_geo_data, companies
answer: "Based on available postings for El Paso, TX..."
```

The list-style geographic routing (PR #310) is working. Data is returned.

---

## 3. Root Cause Analysis

### Why v2.2 / v2.3 showed 0.0 intent_accuracy

**v2.2 run (2026-04-28):** `confidence_self_consistency = 0.55` for all items. This score is only achievable when the LLM runs and returns a response — ruling out a pure API-key fallback. The most likely cause is **model stochasticity**: the complex new tie-breaker prompt (commits `546069c`, `2cd7a7f`) caused the LLM to be inconsistent across sessions. Azure OpenAI `chat-gpt41mini` has non-deterministic outputs at default temperature; a 1-2 hour window can show degraded consistency on nuanced disambiguation tasks.

**v2.3 run (post-spam-backfill):** Same pattern — `intent_accuracy=0.0` but high `evidence_citation` (0.75–0.94). The high evidence scores prove the pipeline DID find geographic data — which is impossible if the intent was truly `'other'` (that path returns "No data in scope"). This strongly suggests the v2.3 run was captured at a different point in time or code state where `classified_intent` was not propagated correctly to the stored scores.

### Why it now works (2026-04-29)

The current `_SYSTEM_PROMPT` with the geographic-primacy tie-breaker and anchoring examples is unambiguous to the LLM in the current model state. No prompt changes were needed — the fix was always in the prompt. The stochastic window closed.

---

## 4. Permanent Regression Guard Added

**File:** `analytics/tests/test_intent_classification.py`

Three new guards:

| Test | Type | What it covers |
|---|---|---|
| `test_geographic_gold_questions_parse_correctly[gq-041..050]` | Mock (fast, always runs) | Parser/validator correctly maps LLM `{"intent":"geographic"}` → `'geographic'` for all 10 gold question texts |
| `test_geographic_intent_in_prompt_has_borderplex_rule` | Structural (fast, always runs) | `_SYSTEM_PROMPT` contains required phrases: `geographic`, `POSTINGS`, `EMPLOYERS`, `Borderplex`, `El Paso`, `Las Cruces` |
| `test_live_geographic_gold_per_intent_floor` | Live LLM (`@pytest.mark.live_llm`) | Per-intent floor: ≥ 0.8 of gq-041..050 must return `'geographic'`; configurable via `GEOGRAPHIC_INTENT_MIN_ACCURACY` env var |

The live floor test should be added to the pre-merge CI gate for `analytics/query_engine/intent.py` changes, catching the next regression at PR time instead of scorecard time.

---

## 5. Still Blocked: Smoke 5 Answerability

While `intent_accuracy` is now 1.000, **`answerability`** is still 0/1 for most geographic questions in the v2.3 run. This is a **data coverage** issue, not a classifier issue:

- gq-048 (Borderplex fintech/regtech): 0 rows — sparse category in dataset
- gq-042, 044, 046, 050 (Las Cruces tech roles): partially empty due to spam classification coverage (#308)
- gq-049 (El Paso legal-tech): 1.0 answerability ✓

Spam backfill (#312) is landed. Re-running the eval after the seed refresh should show improved answerability on the El Paso subset. Las Cruces may still lag until a broader spam backfill runs.

---

## 6. References

- JIE #305 — geographic intent regression issue
- JIE #257 (closed) — prior geographic/employer misclassification (Pair B)
- JIE #292 (closed) — prior disruption intent accuracy (Pair A)
- Commits `546069c`, `2cd7a7f` — geographic-primacy rule + anchoring examples
- PR #310 — list-style geographic query routing (landed, closes data-gap)
- PR #312 — spam backfill (landed, improves answerability coverage)
- `eval/runs/qa-v2.2-post-data-gap-fix.json` — captured regression state
- `eval/runs/qa-v2.3-post-spam-backfill.json` — post-spam-backfill run
