# Q&A Eval Harness — Prompt Iteration Log

Tracks every dataset run against the **LaborPulse Golden Questions** corpus.
One row per `--prompt-version` in the version history; full breakdowns in the
numbered sections below.

> **Canonical source of truth:** Langfuse dataset run `v1-baseline` (and
> successors). This file summarises what Langfuse contains; the scores
> in Langfuse are authoritative if they disagree with a row here.

---

## Version history

| Version | Date | Author | Change summary | Composite mean | Notes |
|---------|------|--------|----------------|----------------|-------|
| `v1-baseline` | 2026-04-23 | Bryan + Emilio | Initial baseline — 90 golden questions, unmodified prompts, in-process pipeline | **0.875** | `answerability` reported separately (0.260, n=50); see v1-baseline section for full breakdown |
| `v2.1-embedding-router` | 2026-04-28 | Pair A / #229 | `label_embedding` pgvector on `canonical_roles`; backfill + persist sync; Q&A router resolves `role_names` via `<=>` with ILIKE fallback (`role_evolution`, `workflow` only) | — | **Local verification, Langfuse run pending.** See `eval/runs/findingsv2.1-embedding-router.md`. Tests: 42 passed; re-cluster env: `CLUSTER_MIN_CLUSTER_SIZE=5`, `CLUSTER_MIN_SAMPLES=2`, `CLUSTER_MIN_TOTAL_POSTINGS=100` + `scripts/run_clustering.py --min-postings 100`. |

---

## Implementation deviations from spec

Deviations recorded here so that anyone reviewing raw Langfuse scores
understands exactly what each automated scorer measures.

---

### DEV-001 — `latency_sla`: continuous decay curve instead of discrete buckets

**Spec (IMP-030 / runbook):**

| Wall-clock latency | Score |
|--------------------|-------|
| < 10 s | 1.0 |
| 10 – 15 s | 0.5 |
| > 15 s | 0.0 |

**Implemented formula (`eval/qa_scoring.py: score_latency_sla`):**

```
score = min(1.0, sla / latency_seconds)
```

Where `sla` defaults to **45 s** (overridable via env var
`QA_EVAL_LATENCY_SLA_SECONDS`).

**Behaviour at key latencies (sla = 45 s):**

| Latency | Spec score | Implemented score |
|---------|------------|-------------------|
| 5 s | 1.0 | 1.0 |
| 10 s | 1.0 | 1.0 |
| 15 s | 0.5 | 1.0 |
| 20 s | 0.0 | 1.0 |
| 30 s | 0.0 | 1.0 |
| 45 s | 0.0 | 1.0 |
| 60 s | 0.0 | 0.75 |
| 90 s | 0.0 | 0.50 |

**Rationale:**
The JIE Analytics Agent pipeline (SQL generation → execution →
synthesis) has a realistic P50 wall-clock latency of 20 – 40 s in the
current stack. The spec's discrete thresholds were calibrated against
a sub-15 s target that the pipeline does not yet meet. Applying those
thresholds to the baseline run would score every question 0.0 on
`latency_sla`, producing a flat signal that carries no information
across prompt versions. The continuous decay curve preserves
proportional signal at current latencies — a 30 s response scores
meaningfully higher than a 90 s response — and will converge to the
spec's range as pipeline optimisations land.

**Re-computation path:**
Raw latency per question is logged in each Langfuse trace comment
(`"latency_sla": "min(1, 45.0s / latency)"`) and in any
`--output-json` dump. To recompute using the spec's discrete buckets,
apply:

```python
def spec_latency_sla(latency_seconds: float) -> float:
    if latency_seconds < 10:
        return 1.0
    if latency_seconds <= 15:
        return 0.5
    return 0.0
```

against the raw latency values stored per trace.

**Status:** Deviation accepted for v1-baseline. Revisit for v2 once
pipeline P50 latency is below 15 s. If the team wants to match the
spec before then, set `QA_EVAL_LATENCY_SLA_SECONDS=15` — the
continuous curve will then approximate the spec's behaviour (15/15 =
1.0, 15/30 = 0.5, 15/90 ≈ 0.17).

---

### DEV-002 — "insufficient data" question coverage: 1 of 20 (Pair C corpus)

**Spec guidance:** At least one question per 10 where the correct
answer is a refusal / "no data available."

**Current state:** Pair C's 20 questions contain **one** explicit
insufficient-data question: `gq-049` (legal-tech postings in El Paso
→ zero results expected). The 10 comparison questions (`gq-051` –
`gq-060`) do not include an equivalent because every comparison
question routes through `skill_demand_weekly` and would produce a
no-data refusal by design until that table is populated — which is
not a meaningful test of refusal behaviour.

**Decision:** 1 of 20 is below the spirit of the spec (should be
≥ 2) but acceptable for v1-baseline given the routing constraint on
comparison questions. Add a second refusal question if the corpus
expands beyond 80 items in a follow-up cycle.

---

### DEV-003 — `source` field dropped from the canonical schema (all 90 rows)

**Spec / Emilio's originals:** `"source": "wfd_archetype"` present on
a subset of items (notably the first-author Pair D rows
`gq-061`–`gq-080`).

**Team-lead call (2026-04-23):** drop `source` from the canonical
golden-question schema. Not required by `upload_qa_dataset.py`
validation, not consistently populated across pairs, and carries no
signal for the automated scorers. Provenance, if needed, belongs in
Langfuse dataset-item metadata — not in the JSON source of truth.

**Current state:** `source` is **not** present on any of the 90
rows. Canonical schema is the 8-key set enforced by the invariant
check run against `eval/qa_golden_questions.json`:

```
{id, question, intent, context, ideal_answer_summary,
 must_include, must_not_include, difficulty}
```

**Applied in:**
- `18783f5 chore(eval): drop non-canonical 'source' field from gq-061-gq-080`
  (2026-04-23) — removed `source` from the 20 Pair D rows that still
  carried it.
- `3f7d657 chore(eval): sync corpus to chore/gq-consolidate-90-medium`
  (2026-04-23) — full-file sync to Gary's consolidated corpus
  (`origin/chore/gq-consolidate-90-medium`); locks the 8-key schema
  across all 90 rows and confirms via the invariant check
  (`schema clean` assertion).

**Decision:** No deviation remaining — spec is now the
`source`-less 8-key schema. Leaving DEV-003 in the log as the
audit trail for the schema change.

---

### DEV-004 — `--use-http` path in `eval/qa_eval.py` regressed by JIE #222 contract drift

**What happened:**
The first v1-baseline launch used `--use-http` against a local
analytics API and returned `pipeline_error: 'http_400'` for all
90/90 items (all Layer-1 scores 0 except `latency_sla=1.0`). The
resulting Langfuse run was deleted; the real v1-baseline was
re-run in-process (see the v1-baseline section).

**Root cause:**
- Commit `d360cda` (Emilio, 2026-04-21 00:49) added
  `execute_qa_item`'s HTTP branch sending
  `{"question", "correlation_id"}` with no custom headers,
  matching the legacy Week 8 `POST /analytics/query` contract.
- Commit `b0cf407` (Juan, 2026-04-21 14:45,
  "Implement LaborPulse POST /analytics/query contract (JIE #222)")
  replaced that shape with the wfd-os contract: required
  `X-Tenant-Id`, `X-User-Email`, `X-Request-Id` headers,
  `X-API-Key` (or `LABORPULSE_ALLOW_NO_API_KEYS=1`), body
  `conversation_id` instead of `correlation_id`.
- The `--use-http` path was not exercised by any test, so the
  regression was silent until v1-baseline kickoff.

**Scope:**
- `--use-http` path only. The default in-process path
  (`run_analytics_qna(session, question, correlation_id)`) was
  unaffected and is what v1-baseline actually ran under.
- No Layer-1 or Layer-2 scores depend on the HTTP wire — the
  scorers operate on the response dict regardless of transport.

**Decision (team lead, 2026-04-23):** fall back to in-process for
v1-baseline; land the HTTP header patch as a follow-up commit
(not a blocker for v1-baseline sign-off). Proposed patch:
`execute_qa_item` to set `Content-Type: application/json` plus
`X-Tenant-Id` / `X-User-Email` / `X-Request-Id` (mirroring
`scripts/smoke/smoke_issue197.py:_laborpulse_headers`) and
rename the body field `correlation_id` → `conversation_id`.

**Follow-up:** tracked as **JIE #255**. Scope includes the
`execute_qa_item` header patch (and `correlation_id` →
`conversation_id` body rename) plus a `--use-http --limit 1
--dry-run` CI smoke so the next `/analytics/query` contract
change can't silently break the harness HTTP path again.

---

## v1-baseline

> **Status: COMPLETE** — 90/90 items scored, run published to
> shared Langfuse. Composite **0.875** (4-metric mean, excludes
> `answerability` per team-lead v1 directive).
> Layer 2 manual scoring pending (Pair C does gq-041 – gq-060;
> A/B/D score their own 20-question blocks).

**Run metadata**

| Field | Value |
|-------|-------|
| Langfuse dataset | LaborPulse Golden Questions |
| Langfuse dataset ID | `cmobvy2aj002pp1070t0zbwir` |
| Run name | `v1-baseline` |
| Langfuse run ID | `27d1ac84-4317-44db-b40f-772c24de5826` |
| Langfuse run URL | <https://langfuse.watechcoalition.org/project/laborpulse-golden-questions/datasets/cmobvy2aj002pp1070t0zbwir/runs/27d1ac84-4317-44db-b40f-772c24de5826> |
| Prompt version | _(system default — no prompt changes)_ |
| Corpus size | **90 / 90** |
| Run date | 2026-04-23 |
| Runner | Bryan + Emilio |
| Harness commit | `a9d352c` (`week-09/qa-eval-harness`, merged `development` at `2bfdb62` for seed fix #253) |
| Transport | in-process `run_analytics_qna` — see **DEV-004** for why not `--use-http` |
| LLM provider | `azure_openai` (default: `chat-gpt41mini`; synthesis: `chat-gpt41`) |
| Output JSON | `eval/runs/qa-v1-baseline.json` |
| Pipeline errors | **0 / 90** |

---

### Layer 1 — Automated scores (overall)

| Score | Weight | n | Mean | Min | p25 | p50 | p75 | Max | Notes |
|-------|--------|---|------|-----|-----|-----|-----|-----|-------|
| `evidence_citation` | 20% | 90 | **0.691** | 0.258 | 0.700 | 0.700 | 0.700 | 0.916 | Flat around 0.700 — dominant "refusal without evidence list" template score |
| `confidence_flags` | 15% | 90 | **1.000** | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | Calibration vs 0.6 + explanations consistently present |
| `intent_accuracy` | 15% | 90 | **0.811** | 0.000 | 0.500 | 1.000 | 1.000 | 1.000 | 63 exact, 20 related (0.5), 7 mismatch (0.0) |
| `latency_sla` | 10% | 90 | **1.000** | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | See DEV-001 — sla=45s, all items well under |
| **Composite (4-metric mean)** | **60%** | 90 | **0.875** | 0.675 | 0.800 | 0.925 | 0.925 | 0.979 | Unweighted mean of the four above |
| `answerability` (#247) | _reported separately_ | **50** | **0.260** | 0.000 | 0.000 | 0.000 | 1.000 | 1.000 | 40 intent-only skipped (trend / role_evolution / emergence / disruption); pass_rate=0.2600 on data-backed |

Raw latency (seconds, for DEV-001 re-computation):

| Statistic | Value |
|-----------|-------|
| p50 latency | 1.60 s |
| p75 latency | 1.88 s |
| p95 latency | 7.12 s |
| Max latency | 25.08 s |
| Questions > 15 s (would score 0.0 under spec's discrete buckets) | **1 / 90** |
| Questions > 45 s (over our continuous-curve sla) | 0 / 90 |

> Latency is fast here because 77/90 items short-circuit at
> synthesis with `sufficiency=no_data` (refused before LLM
> synthesis runs), so wall-clock is dominated by intent
> classification (~1.5 s). The 13 data-backed items that
> reached synthesis account for the long tail (max 25 s).

---

### Layer 1 — Per-intent breakdown

| Intent | n | `evidence_citation` | `confidence_flags` | `intent_accuracy` | `latency_sla` | Composite | `answerability` |
|--------|---|---------------------|--------------------|-------------------|---------------|-----------|-----------------|
| `geographic` | 10 | 0.624 | 1.000 | **0.500** | 1.000 | **0.781** | 1.000 (10/10 data-backed) |
| `comparison` | 10 | 0.700 | 1.000 | 1.000 | 1.000 | 0.925 | 0.000 (0/10 data-backed) |
| `trend` | 10 | 0.700 | 1.000 | 1.000 | 1.000 | 0.925 | _skipped (intent-only)_ |
| `role_evolution` | 10 | 0.700 | 1.000 | 0.900 | 1.000 | 0.900 | _skipped (intent-only)_ |
| `disruption` | 10 | 0.700 | 1.000 | **0.500** | 1.000 | **0.800** | _skipped (intent-only)_ |
| `emergence` | 10 | 0.700 | 1.000 | 0.700 | 1.000 | 0.850 | _skipped (intent-only)_ |
| `employer` | 10 | 0.716 | 1.000 | 0.900 | 1.000 | 0.904 | 0.200 (2/10 data-backed) |
| `curriculum` | 10 | 0.700 | 1.000 | 1.000 | 1.000 | 0.925 | 0.000 (0/10 data-backed) |
| `workflow` | 10 | 0.675 | 1.000 | 0.800 | 1.000 | 0.869 | 0.100 (1/10 data-backed) |
| **All** | **90** | **0.691** | **1.000** | **0.811** | **1.000** | **0.875** | **0.260** (13/50 data-backed; 40 skipped) |

---

### Layer 1 — Per-score weakness analysis

**`evidence_citation` (mean 0.691)**
- Weakest intent: `workflow` (0.675); next lowest `geographic` (0.624).
- Dominant failure pattern: 77/90 responses are synthesis
  refusals ("No data in scope for the selected filters") which
  score a flat **0.700** by the scoring-module's "refusal
  without evidence list" rule. That flat 0.700 is the source of
  the near-constant distribution (p25 = p50 = p75 = 0.700).
  Real signal will emerge once more data-backed intents actually
  return rows — today `evidence_citation` is essentially a
  "refused vs answered with evidence" flag, not a citation-quality
  metric.
- `geographic` drops below 0.700 because 3/10 items did produce
  evidence but with partial `must_include` coverage (e.g. gq-046,
  gq-044).

**`confidence_flags` (mean 1.000)**
- Perfect across all intents. `confidence_flagged_low=True` is
  attached to every refusal with a populated
  `confidence_explanation`, and the calibration-vs-0.6 check
  passes trivially because the intent-classification confidence
  itself is always ≥ 0.7 in this run.
- Caveat: this metric is not measuring much at v1 — it will
  become discriminating once we get enough answered (non-refused)
  responses where the confidence *isn't* flagged low.

**`intent_accuracy` (mean 0.811)**
- Weakest intent: `geographic` (0.500) and `disruption` (0.500);
  tied floor.
- Exact hits: 63/90; related (0.5): 20/90; mismatch (0.0): 7/90.
- Most common misclassifications:
  - **`geographic → employer` (×10)** — every single `geographic`
    question was classified as `employer`. Driven by questions
    framed "Which employers in {Borderplex city}..." — the
    classifier locks onto the "employer" entity and drops the
    geographic dimension. Top candidate for prompt iteration in v2.
  - `disruption → trend` (×3), `disruption → role_evolution` (×2),
    `disruption → comparison` (×2, full mismatch): `disruption`
    prompts need sharper differentiation from adjacent temporal
    intents.
  - `emergence → {curriculum, comparison, employer}` (×1 each,
    full mismatch): three of ten `emergence` items fell outside
    the related-intents set.
- Full-mismatch (0.0) pairs: disruption→comparison (×2),
  workflow→curriculum, employer→comparison, emergence→curriculum,
  emergence→comparison, emergence→employer.

**`latency_sla` (mean 1.000, continuous curve, sla = 45 s)**
- Weakest intent: none — all intents at 1.000.
- Questions above 60 s: 0.
- Questions above 15 s (spec's discrete-bucket floor): **1 / 90**
  (max 25.08 s); p95 = 7.12 s. Under the spec's discrete buckets
  the headline score would be ~0.989 vs our 1.000.
- See DEV-001 for raw-latency re-computation.

**`answerability` (#247, mean 0.260, n=50, pass_rate 0.2600)**
- 40 / 90 items correctly **skipped** (intent-only:
  trend / role_evolution / emergence / disruption — these flip
  to data-backed once posted_date ingestion lands, per the
  `INTENT_TO_DATA_BACKED` map in `eval/qa_scoring.py`).
- Of 50 data-backed items, 13 returned rows (pass 1.0) and 37
  returned zero rows (pass 0.0). The 37 zero-row cases are
  concentrated in `comparison` (0/10), `curriculum` (0/10),
  `workflow` (1/10 returned), and `employer` (2/10 returned).
- This is the gap the team-lead wanted visible: the pipeline
  successfully routes most data-backed intents, but the current
  corpus + data doesn't support synthesis for most of them. The
  score is the explicit tracker to flip metrics up over the next
  ingestion / prompt cycles.

---

### Layer 1 — Top 10 worst-performing questions (by composite)

_Candidates for prompt iteration in Week 10. All ties at 0.675
share the same failure mode: intent misclassification
(`intent_accuracy = 0.0`) combined with the 0.700 "refusal
without evidence list" on `evidence_citation`._

| Rank | ID | Intent (expected) | Classified | Composite | EC | CF | IA | LS | `ans` | Failure note |
|------|----|-------------------|------------|-----------|-----|-----|-----|-----|-------|--------------|
| 1 | gq-002 | disruption | comparison | 0.675 | 0.70 | 1.00 | 0.00 | 1.00 | _skip_ | AI-assistant tools required/preferred — classifier routed as comparison |
| 2 | gq-010 | disruption | comparison | 0.675 | 0.70 | 1.00 | 0.00 | 1.00 | _skip_ | Classifier locked on "compare" surface cue |
| 3 | gq-016 | emergence | employer | 0.675 | 0.70 | 1.00 | 0.00 | 1.00 | _skip_ | Full mismatch; emergence→employer |
| 4 | gq-017 | emergence | comparison | 0.675 | 0.70 | 1.00 | 0.00 | 1.00 | _skip_ | Full mismatch; emergence→comparison |
| 5 | gq-020 | emergence | curriculum | 0.675 | 0.70 | 1.00 | 0.00 | 1.00 | _skip_ | Full mismatch; emergence→curriculum |
| 6 | gq-067 | employer | comparison | 0.675 | 0.70 | 1.00 | 0.00 | 1.00 | 0.0 | Academic-employer comparison framing tripped the classifier |
| 7 | gq-085 | workflow | curriculum | 0.675 | 0.70 | 1.00 | 0.00 | 1.00 | 0.0 | Defense-IT framing; workflow→curriculum |
| 8 | gq-046 | geographic | employer | 0.689 | 0.26 | 1.00 | 0.50 | 1.00 | 1.0 | geographic→employer (related) + partial must_include coverage |
| 9 | gq-090 | workflow | employer | 0.739 | 0.45 | 1.00 | 0.50 | 1.00 | 1.0 | workflow→employer (related); partial evidence |
| 10 | gq-044 | geographic | employer | 0.748 | 0.49 | 1.00 | 0.50 | 1.00 | 1.0 | geographic→employer (related); partial evidence |

Questions with `pipeline_error` (scored 0.0 across all dimensions):

| ID | Error type | Latency (s) |
|----|------------|-------------|
| _none_ | — | — |

**0 pipeline errors across 90 items.** The first launch attempt
(via `--use-http`) returned `http_400` on every item — see
**DEV-004**. That attempt's run was deleted in Langfuse and is
not represented here.

---

### Layer 2 — Manual scores

_Each pair scores their own 20 questions in the Langfuse UI._

| Score | Weight | Pair A mean | Pair B mean | Pair C mean | Pair D mean | Overall mean |
|-------|--------|-------------|-------------|-------------|-------------|--------------|
| `correctness` | 20% | — | — | — | — | — |
| `decision_relevance` | 10% | — | — | — | — | — |
| `followup_quality` | 10% | — | — | — | — | — |

**Pair C (Bryan + Emilio) detail:**

| ID | Intent | `correctness` | `decision_relevance` | `followup_quality` | Scorer | Notes |
|----|--------|---------------|----------------------|--------------------|--------|-------|
| gq-041 | geographic | — | — | — | Bryan | |
| gq-042 | geographic | — | — | — | Bryan | |
| gq-043 | geographic | — | — | — | Bryan | |
| gq-044 | geographic | — | — | — | Bryan | |
| gq-045 | geographic | — | — | — | Bryan | |
| gq-046 | geographic | — | — | — | Bryan | |
| gq-047 | geographic | — | — | — | Bryan | |
| gq-048 | geographic | — | — | — | Bryan | |
| gq-049 | geographic | — | — | — | Bryan | Refusal expected (zero legal-tech postings) |
| gq-050 | geographic | — | — | — | Bryan | |
| gq-051 | comparison | — | — | — | Emilio | |
| gq-052 | comparison | — | — | — | Emilio | |
| gq-053 | comparison | — | — | — | Emilio | |
| gq-054 | comparison | — | — | — | Emilio | |
| gq-055 | comparison | — | — | — | Emilio | Will refuse until `role_snapshot_weekly` populated (see gq-055 notes) |
| gq-056 | comparison | — | — | — | Emilio | |
| gq-057 | comparison | — | — | — | Emilio | Will refuse until `sector_summary_weekly` populated (see gq-057 notes) |
| gq-058 | comparison | — | — | — | Emilio | |
| gq-059 | comparison | — | — | — | Emilio | |
| gq-060 | comparison | — | — | — | Emilio | |

---

### Observations and failure patterns

1. **Top failure mode — evidence citation:** flat 0.700 "refusal
   without evidence list" dominates (77/90 items refused at
   synthesis with `sufficiency=no_data`). Until more data-backed
   intents return rows, `evidence_citation` is effectively a
   refused-vs-answered flag, not a citation-quality metric.
   Tracked by `answerability` — once that rises, `evidence_citation`
   will become discriminating.
2. **Top failure mode — intent accuracy:** **geographic → employer
   (10/10)** is the single largest systematic confusion. Every
   `geographic` question (gq-041 – gq-050), all framed as "Which
   employers in {city}…", was routed to `employer`. Next-largest
   groups: `disruption → {trend | role_evolution | comparison}`
   (7/10 non-exact) and `emergence → {curriculum | comparison |
   employer}` (3 full mismatches). High-impact v2 prompt
   iteration: tighten the classifier's geographic-vs-employer
   signal and clarify `disruption`-vs-temporal-siblings boundaries.
3. **Latency distribution vs spec thresholds:** 1 / 90 questions
   (1.1%) returned responses > 15 s (max 25.08 s, p95 7.12 s);
   under the spec's discrete buckets that one would score 0.0
   and the overall mean would drop from 1.000 to ~0.989. Raw
   latency values are in the `latency_seconds` field of each
   item in `eval/runs/qa-v1-baseline.json` for re-computation
   per DEV-001.
4. **Comparison questions with expected refusals (gq-055,
   gq-057):** both refused as expected
   (`sufficiency=no_data`; `role_snapshot_weekly` and
   `sector_summary_weekly` still unpopulated). They score 0.700
   on `evidence_citation` (refusal template), 1.000 on
   `confidence_flags`, 1.000 on `intent_accuracy` (comparison
   intent correctly identified), and 0.000 on `answerability`
   (comparison is data-backed and returned 0 rows). Behaviour
   matches the DEV-002 design note.
5. **Highest-impact prompt change for v2:** the
   geographic-vs-employer boundary fix above is the single
   biggest lever on the composite score (10/90 items currently
   capped at `intent_accuracy=0.5`). Fixing that alone would
   lift overall `intent_accuracy` from 0.811 to ~0.867 and
   composite from 0.875 to ~0.889 without any data-ingestion
   change.
6. **#197 guard behaviour:** the `role_classification != 'N/A Not
   an IT role'` guard hint was attached on every role-based
   query (`issue197_orm_guard_hint_attached` / 
   `issue197_sql_guard_hint_in_router_context` log events
   observed throughout the run). No bad labels leaked into any
   evidence row this baseline confirms.
7. **Cost:** run cost is dominated by intent classification
   (~$0.00025 per item × 90 ≈ $0.02) plus synthesis on the 13
   data-backed items that weren't refused. Total real-LLM spend
   for v1-baseline is in the low single-digit cents —
   comfortably under the team-lead's approved budget.

---

### Sign-off

| Role | Name | Date | Notes |
|------|------|------|-------|
| Layer 1 runner | Bryan | 2026-04-23 | Ran `python -m eval.qa_eval --prompt-version v1-baseline --output-json eval/runs/qa-v1-baseline.json --worst-n 20` (in-process; see DEV-004 for why not `--use-http`) |
| Layer 2 scorer (geographic) | Bryan | _pending_ | Manual scores for gq-041 – gq-050 |
| Layer 2 scorer (comparison) | Emilio | _pending_ | Manual scores for gq-051 – gq-060 |
| Baseline editor | Bryan + Emilio | 2026-04-23 | Wrote this section |

---

## How to add a future run

1. Run `eval/qa_eval.py --prompt-version <semantic-tag> --output-json runs/<tag>.json`.
2. Add a row to the **Version history** table.
3. Copy the **v1-baseline** section template below this section, rename the
   header to the new version, and fill in scores from Langfuse / the JSON dump.
4. Note the specific prompt change and the hypothesis being tested.
5. Commit the log alongside the prompt file change (or immediately after the
   run if no prompt changed).

**Comparison guidance:** Look at per-intent score distributions (min / p25 /
p50 / p75 / max), not only overall means. A run with a higher overall mean but
a collapsing p25 has gotten *worse* on hard questions. Require consistent
per-intent improvement before declaring a version better. At n ≈ 80, a
2-point composite delta is within noise.

---

## Changelog

| Date | Who | Change |
|------|-----|--------|
| 2026-04-22 | Bryan | Created skeleton; documented DEV-001 (latency formula), DEV-002 (refusal coverage), DEV-003 (`source` field); v1-baseline section stubbed pending full 80-question corpus |
| 2026-04-23 | Bryan | Rewrote DEV-003 to reflect team-lead call to drop `source` from the canonical schema across all 90 rows (commits `18783f5` + `3f7d657`). No behaviour change to scorers; audit trail only. |
| 2026-04-23 | Bryan + Emilio | **v1-baseline run complete** — 90/90 items, composite 0.875, `answerability` 0.260 (n=50, 40 skipped). Filled the v1-baseline section (overall scores, per-intent breakdown, worst-10, observations, sign-off). Added **DEV-004** documenting the `--use-http` regression from JIE #222 contract drift; run landed via in-process path after the HTTP retry was discarded. Output JSON at `eval/runs/qa-v1-baseline.json`; Langfuse run `27d1ac84-4317-44db-b40f-772c24de5826`. |
