# v2.3 Eval Findings — Post Spam-Classification Backfill (JIE #308)

**Run name:** `qa-v2.3-post-spam-backfill`
**Date:** 2026-04-28
**Subset:** 10 Borderplex / agentic-era geographic questions (`gq-041..050`)
**Branch / scorer version:** `development` (post PR #311 merge + Phase 6.2 backfill)
**Run output:** [`qa-v2.3-post-spam-backfill.json`](qa-v2.3-post-spam-backfill.json)

---

## Hypothesis Being Tested

After PR #311 fixed the structural workflow (`spam_tier` now persisted on every promotion path; new sweeper for unclassified rows; loud guard) and Phase 6.2 ran `scripts/backfill_spam_classification.py --apply` against the 4,710 historical rows, re-run Bryan's `gq-041..050` subset to verify whether the spam-coverage gap was the load-bearing blocker after #244+#289+#306 closed the data-and-routing layers.

**Prediction:** if spam coverage was the only remaining structural blocker, `evidence_citation` rises sharply on this subset (synthesis now has rows to cite); `intent_accuracy` stays stuck at 0.000 because Pair C's classifier regression (#305) is still active and unrelated to spam.

---

## Result — confirmed: data layer fully unblocked, classifier regression still owns the residual

The data-and-routing layer is now end-to-end operational. **`evidence_citation` jumped from 0.074 (v2.2) → 0.752 (v2.3) — a 10x improvement** on the same gq-041..050 subset. Synthesis has rows to cite, the citations land, the answers are grounded. As predicted, `intent_accuracy` stays at 0.000 because the classifier regression (JIE #305) hasn't been touched — that's Pair C's work.

The remaining `answerability=0.100` is also classifier-driven: 9 of 10 questions are misclassified as `intent='other'`, which falls through to the unrouted path and never reaches the geographic per-posting query. The single question that reaches geographic routing returns plenty of data. **Once #305 is fixed, the same v2.3 scorecard should jump to ≥0.7 answerability with no further data-layer work needed.**

---

## Layer 1 — Database invariants (post-Phase-6.2)

All six smoke tests in `eval/tests/test_smoke_borderplex_data_gap.py` pass:

| Invariant | Pre-#308 | Post-#308 | Status |
|---|---:|---:|:---:|
| `normalized_jobs.zip_code` populated where city+state present | 97.9% | 97.9% | ✅ unchanged |
| `job_postings.zip_code` populated | 76.7% | 76.7% | ✅ unchanged |
| Orphan `normalized_jobs` rows | 0 | 0 | ✅ unchanged |
| El Paso postings reachable via `postal_geo_data` join | 1,015 | 1,015 | ✅ unchanged |
| `geo_demand_weekly` row count | 6 | 9 | ✅ +3 (post-refresh) |
| **NEW:** `spam_tier` populated | **0% (0/4,710)** | **100% (4,710/4,710)** | ✅ **NEW** |
| **NEW:** `spam_tier = 'clean'` row count | **27** | **4,648** | ✅ **NEW** |

Post-backfill spam distribution:

| Tier | Count | `is_spam` | Routes through PR #310's filter? |
|---|---:|:---:|:---:|
| clean | 4,648 (98.7%) | FALSE | ✅ surfaced |
| flagged | 62 (1.3%) | NULL | ❌ HITL queue (correct per Gary's rule) |
| uncertain | 0 | — | — |
| (NULL — unclassified) | 0 | — | — |

**Per-region clean rows reachable for Bryan's gq-041..050:**

| Region | Pre-#308 | Post-#308 |
|---|---:|---:|
| El Paso (clean) | 22 | **1,008** (45×) |
| Las Cruces (clean) | 0 | **1,416** (∞×) |

---

## Layer 2 — Answer quality on `gq-041..050`

### Three-way scorecard

| Metric | v2.2 (post data-gap fix) | **v2.3 (post spam-coverage)** | Δ v2.3 vs v2.2 | Smoke 4 target | Met? |
|---|:---:|:---:|:---:|:---:|:---:|
| `intent_accuracy` | 0.000 | **0.000** | 0 | ≥ 0.75 | ❌ (blocked by #305) |
| `evidence_citation` | 0.074 | **0.752** | **+0.678** ⭐ | ≥ 0.55 | ✅ |
| `confidence_self_consistency` | 0.595 | **0.910** | +0.315 | — | — |
| `confidence_in_expected_range` | 0.223 | **0.724** | +0.501 | — | — |
| `latency_sla` | 1.000 | 1.000 | 0 | — | ✅ |
| `answerability` *(n=10 data-backed)* | 0.100 | **0.100** | 0 | ≥ 0.50 | ❌ (blocked by #305) |

### Direct probes (bypassing the broken intent classifier)

The same questions, asked outside the eval harness so the classifier's `'other'` mismatch doesn't short-circuit routing:

| Question | v2.2 result | **v2.3 result** |
|---|---|---|
| "Show all El Paso, TX postings for AI agent developer, prompt engineer, or LLM engineer roles posted in the last 6 months" (gq-041) | 17 evidence items, "no postings explicitly titled" caveat | **101 evidence items**, cites OpenClaw AI Agent Developer, GenAI Full-Stack Lead, Scale-Secure AI Platform Engineer, Enterprise AI Chatbot, etc. |
| "List all Las Cruces, NM AI/ML researcher and applied-scientist postings, highlighting any university-affiliated employers such as NMSU, UTEP, or EPCC" (gq-050) | "No data in scope" (refusal) | **101 evidence items**, cites Principal Applied Scientist, Responsible AI Research Scientist, Senior Applied ML Engineer, etc. **Includes the gold-required NMSU/UTEP/EPCC caveat verbatim.** |

The pipeline now consistently produces real, cited, ground-truth-grounded answers when reach the geographic-list-style routing. The `intent_accuracy=0.000` in the eval scorecard is not because the synthesis is broken — it's because the classifier short-circuits the question to the wrong handler before synthesis runs.

---

## What this confirms about the dependency chain

| Issue | Status | What it gates |
|---|---|---|
| #244 — `_resolve_zip_code` + promotion SQL | ✅ closed (PR #304) | data layer: zip propagation |
| #289 — silent-skip + `promoted_at` | ✅ closed (PR #301 + #303) | data layer: orphan recovery |
| #306 — list-style routing | ✅ closed (PR #310) | routing layer: geographic per-posting |
| **#308 — spam coverage** | ✅ **closed (PR #311 + this PR)** | **answer quality: synthesis can cite** |
| #305 — intent classifier regression on geographic | 🔴 **OPEN — Pair C** | answer quality: classifier picks the right handler |
| #309 — embedding role match (replace `_tokenize_role_name`) | 🟡 OPEN, P3 — Pair A | answer quality: niche role-name resolution |

The data-layer + routing-layer fixes (#244/#289/#306/#308) are now complete end-to-end. The only blocker left for full Smoke 5 success on Bryan's gq-041..050 subset is **#305** (Pair C's intent-classifier prompt regression). Once that's fixed, the v2.3 scorecard should re-run cleanly with `intent_accuracy ≥ 0.75` and `answerability ≥ 0.7` without any further pipeline-side changes.

---

## Cohort handoff (after PR-2 merges)

Fresh fixtures committed at `scripts/pg-seed-data/fixtures/*.json`. Devs reseed locally:

```bash
git pull origin development
python scripts/pg-seed-data/seed_pg_database.py    # idempotent
python -m pytest eval/tests/test_smoke_borderplex_data_gap.py -v   # all 6 pass
```

For Pair C (Bryan + Emilio): re-run `gq-041..050` against the fresh fixtures + after fixing #305 — expect the v2.3 scorecard's `evidence_citation=0.752` floor to hold and `intent_accuracy` + `answerability` to climb into target territory.

For Pair A (Ángel + Fabian): #309 (embedding role match) is now genuinely useful — the data layer it depends on is fully populated. The substring tokenization in PR #310's `_tokenize_role_name` works (gq-041 returned 101 cited postings), but #299's `_match_canonical_role` semantic pattern would tighten precision.

For Pair D (Juan + Enrique): the `canonical_role_id` propagation gap (Juan's blocker) can now be re-investigated against a populated database — previously many rows had no spam classification and were excluded from his query path.

---

## Closes

- JIE #308 — spam-classification coverage gap (~4,683 of 4,710 rows NULL `is_spam`)

Closed at the **data layer**: 100% of `dbo.job_postings` rows now carry a `spam_tier` label; 4,648 are confirmed clean and surface through PR #310's strict `is_spam = FALSE` filter; 62 flagged stay quarantined for HITL review per Gary's standing rule.
