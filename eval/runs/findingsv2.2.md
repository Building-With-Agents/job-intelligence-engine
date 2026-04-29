# v2.2 Eval Findings — Post Data-Gap Fix (JIE #244 + #289)

**Run name:** `qa-v2.2-post-data-gap-fix`
**Date:** 2026-04-28
**Subset:** 10 Borderplex / agentic-era geographic questions (`gq-041..050`)
**Branch / scorer version:** `development` (post PR #301 + #303 + #304 merge)
**Run output:** [`qa-v2.2-post-data-gap-fix.json`](qa-v2.2-post-data-gap-fix.json)

---

## Hypothesis Being Tested

After PR #301 closed the silent-skip bug in the live promotion loop, PR #303 backfilled the 469 existing `normalized_jobs` orphans, and PR #304 fixed `_resolve_zip_code` + threaded `zip_code` through every promotion SQL constant, re-run Bryan's `gq-041..050` subset to verify whether the **upstream data gap** (orphan rows, NULL `zip_code`, missing sub-region attribution) was the root cause of the *"No data in scope"* refusals on Borderplex / agentic-era questions.

**Prediction:** if the data layer was the only blocker, refusal rate drops to <20% on this subset and `answerability` rises from v2.1's ~0.06 (on this subset) to ≥0.50.

---

## Result — partial: data layer fixed, query-routing layer still blocks

The data layer is unambiguously fixed end-to-end (smoke-test invariants pass). But the answer-quality scorecard on `gq-041..050` shows the pipeline still refuses most of these questions, because **the queries are list-style** ("Show all El Paso postings for AI agent developer / prompt engineer / LLM engineer") and the **query router currently sends them to `geo_demand_weekly` — an aggregate table** — which has no per-posting detail, so synthesis returns *"No data in scope."*

In other words: Bryan's *"No data in scope"* complaint conflated two layers. PR #301/#303/#304 closed the data layer (the layer this fix arc was scoped to). The query-routing layer is a separate blocker, addressed in a new follow-up issue [tracked below](#open-blockers-for-bryans-smoke-5-after-this-pr).

---

## Layer 1 — Data invariants ✅ PASS

These are the four SQL invariants enforced by [`eval/tests/test_smoke_borderplex_data_gap.py`](../tests/test_smoke_borderplex_data_gap.py). Pre-fix vs post-fix on Gary's source-of-truth DB:

| Invariant | Pre-fix | Post-fix | Threshold | Status |
|---|---:|---:|---:|:---:|
| `normalized_jobs.zip_code` populated where city+state present | 0 / 4,506 (**0.0%**) | 4,420 / 4,516 (**97.9%**) | ≥ 90% | ✅ |
| `job_postings.zip_code` populated | 0 / 4,249 (**0.0%**) | 3,612 / 4,710 (**76.7%**) | populated | ✅ |
| Orphan `normalized_jobs` rows (no matching `job_postings`) | **469** | **0** | ≤ 5 | ✅ |
| El Paso postings reachable via `postal_geo_data` join | **0** | **1,015** | ≥ 100 | ✅ |
| `geo_demand_weekly` row count (after `refresh_aggregates`) | 3 | **6** | ≥ 6 | ✅ |

Every CI run will gate on these. If a future change re-introduces orphans or breaks zip-code propagation, the smoke fails before the eval scorecard runs.

---

## Layer 2 — Answer quality on `gq-041..050` ⚠ WORSE THAN EXPECTED

| Metric | v2.1 baseline (90 items) | **v2.2 — gq-041..050 only** | Smoke 4 target | Met? |
|---|:---:|:---:|:---:|:---:|
| `intent_accuracy` | 0.7556 | **0.000** | ≥ 0.75 | ❌ |
| `evidence_citation` | 0.4483 | **0.074** | ≥ 0.55 | ❌ |
| `confidence_self_consistency` | 1.0000 | **0.595** | — | — |
| `latency_sla` | 1.0000 | **1.000** | — | ✅ |
| `answerability` *(n=10 data-backed)* | 0.3400 | **0.100** | ≥ 0.50 | ❌ |
| `correct_refusal` *(n=0 intent-only)* | 0.2500 | N/A (no intent-only items) | — | — |

> The v2.1 averages are over all 90 questions. The v2.2 row is the 10-question Borderplex subset only — it is *not* a like-for-like comparison, but it is the slice that surfaced Bryan's complaint and the slice the data fix was supposed to unblock.

### What the failure mode is

- **All 10 questions classified `intent='other'`** instead of `'geographic'` (despite the gold record's `expected_intent='geographic'`). This is a regression in the intent classifier prompt — see "Open blockers" below.
- **9 of 10 returned `"No data in scope for the selected filters."`** despite `geo_demand_weekly` being populated. The pipeline routed to the aggregate table even though the questions ask for per-posting lists.
- The single direct probe `"How many job postings are in El Paso?"` (count-style, not list-style) **does** return a real answer ("between 45 and 47 postings") — so the data layer is genuinely reachable; the routing layer is what's blocking the list-style queries.

---

## Three-way scorecard (caveat: subsets differ)

| Metric | v2 (empty tables, 90 items) | v2.1 (post-seed, 90 items) | **v2.2 (post-data-gap, 10 items)** |
|---|:---:|:---:|:---:|
| intent_accuracy | 0.7556 | 0.7556 | **0.000** ⚠ regression on subset |
| evidence_citation | 0.3368 | 0.4483 | **0.074** ⚠ regression on subset |
| answerability *(data-backed)* | 0.0600 | 0.3400 | **0.100** ⚠ regression on subset |

The v2.2 numbers are **lower** than v2.1, but the comparison is unfair: v2.1 averaged over 90 mixed-difficulty questions; v2.2 only ran the 10 hardest geographic-intent questions. The *expected* v2.2 lift was on this subset specifically vs. the same 10 questions in v2.1 — but since the per-question v2.1 results aren't broken out by ID in the v2.1 findings, we can't recover that comparison without re-running the full 90-question scorecard.

**Recommendation:** when Bryan runs Smoke 5, also re-run the full 90-question dataset against current `development` so the v2 / v2.1 / v2.2 columns are like-for-like, and so the routing-layer regression in `intent='other'` shows up against the full mix (not just on geo-intent questions where it dominates).

---

## Open blockers for Bryan's Smoke 5 (after this PR)

These are filed as separate follow-up issues — they are *not* in scope for the #244+#289 fix arc, which was bounded to the data layer:

1. **JIE [#305](https://github.com/Building-With-Agents/job-intelligence-engine/issues/305) — eval/router: geographic intent regression on v2.2 (10/10 misclassified as `'other'`)**
   - Owner: Pair C (Bryan + Emilio) — recent prompt iteration on `analytics/query_engine/intent.py` (commits `2cd7a7f`, `546069c`).
   - Cited closed predecessors: #257 (Pair B fix in Week 9), #292 (Pair A fix on disruption intent).
   - Smoke 5 evidence: 10/10 geographic gold questions → `intent='other'`. Probably a prompt regression, not structural.

2. **JIE [#306](https://github.com/Building-With-Agents/job-intelligence-engine/issues/306) — eval/router: list-style geographic queries route to aggregate tables and refuse**
   - Owner: Pair D (Juan + Enrique) — owns `analytics/query_engine/routing.py`. Pair C collaborates from the eval side.
   - Smoke 5 evidence: gq-041..050 all routed to `geo_demand_weekly` → "No data in scope". The same pipeline answers count-style questions correctly when routed to `job_postings`.
   - Suggested fix: add an intent sub-classification (count-style vs list-style) and route list-style `geographic` to a per-posting query against `job_postings JOIN postal_geo_data`.

The plan's stated success gate — "Bryan's golden questions get real answers instead of refusals" — is **partially** met:
- Simple count-style geographic questions: now return real answers ✅
- List-style per-posting geographic questions: still refuse ❌ (waiting on routing layer)

---

## Cohort handoff (after this PR merges)

Fresh fixtures committed at `scripts/pg-seed-data/fixtures/*.json`. Every dev refreshes their local DB:

```bash
git pull origin development
python scripts/pg-seed-data/seed_pg_database.py    # idempotent
python -m pytest eval/tests/test_smoke_borderplex_data_gap.py -v
```

For Pair C (Bryan + Emilio): re-run `gq-041..050` against the fresh fixtures to confirm the data layer locally, then iterate on the intent classifier prompt against the new follow-up issue.

For Pair A (Ángel + Fabian): the embedding-router work (#229 Step 7) now has populated `zip_code` + `postal_geo_data` joins to consume.

For Pair D (Juan + Enrique): the curriculum-path role resolution can now hit the previously-orphaned El Paso rows. Re-run the smoke against "Data Center Network Infrastructure Engineer" once the routing-layer follow-up lands.

---

## Closes

- JIE #244 — `_resolve_zip_code` truncated full state names + promotion SQL omitted `zip_code`
- JIE #289 — silent-skip in live promotion loop + 469 orphan rows

Both closed at the **data layer**. The query-routing-layer answer quality on list-style geographic questions is tracked as a separate follow-up.
