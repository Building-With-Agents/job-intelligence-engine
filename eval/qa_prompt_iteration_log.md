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
| `v1-baseline` | _pending_ | Bryan + Emilio | Initial baseline — 80 golden questions, unmodified prompt | _pending_ | Awaiting full 80-question corpus from pairs A, B, D |

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

### DEV-003 — `source` field omitted from Pair C (Bryan) geographic questions

**Spec / Emilio's originals:** `"source": "wfd_archetype"` present on
each item.

**Current state:** Bryan's 10 geographic questions (`gq-041` –
`gq-050`) do not carry the `source` field. The field is not in
`_REQUIRED` validation in `upload_qa_dataset.py` and is cosmetic
metadata.

**Decision:** Omit for consistency within Pair C; the field is not
scored. Standardise to include it if the schema is formalised in a
later sprint.

---

## v1-baseline

> **Status: PENDING** — awaiting full 80-question corpus from pairs A,
> B, and D. Pair C's 20 questions are authored and upload-validated.
> Run `eval/qa_eval.py --prompt-version v1-baseline` once all 80 items
> are in Langfuse.

**Run metadata**

| Field | Value |
|-------|-------|
| Langfuse dataset | LaborPulse Golden Questions |
| Run name | `v1-baseline` |
| Prompt version | _(system default — no prompt changes)_ |
| Corpus size | _80 / 80_ |
| Run date | _pending_ |
| Runner | Bryan + Emilio |
| Harness commit | _pending_ |
| Output JSON | _pending (`--output-json` path)_ |

---

### Layer 1 — Automated scores (overall)

| Score | Weight | Mean | Min | p25 | p50 | p75 | Max | Notes |
|-------|--------|------|-----|-----|-----|-----|-----|-------|
| `evidence_citation` | 20% | — | — | — | — | — | — | |
| `confidence_flags` | 15% | — | — | — | — | — | — | |
| `intent_accuracy` | 15% | — | — | — | — | — | — | |
| `latency_sla` | 10% | — | — | — | — | — | — | See DEV-001 |
| **Composite** | **60%** | — | — | — | — | — | — | Weighted sum of above |

Raw latency (seconds, for DEV-001 re-computation):

| Statistic | Value |
|-----------|-------|
| p50 latency | — |
| p75 latency | — |
| p95 latency | — |
| Max latency | — |
| Questions scoring 0.0 at discrete spec thresholds (> 15 s) | — |

---

### Layer 1 — Per-intent breakdown

| Intent | n | `evidence_citation` mean | `confidence_flags` mean | `intent_accuracy` mean | `latency_sla` mean | Composite mean |
|--------|---|--------------------------|-------------------------|------------------------|---------------------|----------------|
| `geographic` | — | — | — | — | — | — |
| `comparison` | — | — | — | — | — | — |
| `trend` | — | — | — | — | — | — |
| `role_evolution` | — | — | — | — | — | — |
| `disruption` | — | — | — | — | — | — |
| `emergence` | — | — | — | — | — | — |
| `employer` | — | — | — | — | — | — |
| `curriculum` | — | — | — | — | — | — |
| `workflow` | — | — | — | — | — | — |
| **All** | **80** | — | — | — | — | — |

---

### Layer 1 — Per-score weakness analysis

_Fill after run. For each score, note the dominant failure pattern._

**`evidence_citation`**
- Weakest intent: —
- Dominant failure pattern: —

**`confidence_flags`**
- Weakest intent: —
- Dominant failure pattern: —

**`intent_accuracy`**
- Weakest intent: —
- Confusion pairs (most common mismatch): —

**`latency_sla`** _(continuous curve, sla = 45 s)_
- Weakest intent: —
- Questions above 60 s: —
- Note: see DEV-001 for raw latency log and re-computation instructions.

---

### Layer 1 — Top 10 worst-performing questions (by composite)

_Candidates for prompt iteration in Week 10._

| Rank | ID | Intent | Composite | `evidence_citation` | `confidence_flags` | `intent_accuracy` | `latency_sla` | Failure note |
|------|----|--------|-----------|---------------------|--------------------|-------------------|---------------|--------------|
| 1 | — | — | — | — | — | — | — | — |
| 2 | — | — | — | — | — | — | — | — |
| 3 | — | — | — | — | — | — | — | — |
| 4 | — | — | — | — | — | — | — | — |
| 5 | — | — | — | — | — | — | — | — |
| 6 | — | — | — | — | — | — | — | — |
| 7 | — | — | — | — | — | — | — | — |
| 8 | — | — | — | — | — | — | — | — |
| 9 | — | — | — | — | — | — | — | — |
| 10 | — | — | — | — | — | — | — | — |

Questions with `pipeline_error` (scored 0.0 across all dimensions):

| ID | Error type | Latency (s) |
|----|------------|-------------|
| — | — | — |

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

_Fill after run and after manual scoring._

1. **Top failure mode — evidence citation:** —
2. **Top failure mode — intent accuracy:** —
3. **Latency distribution vs spec thresholds:** — questions (— %) returned
   responses > 15 s; under the spec's discrete buckets they would score 0.0.
   Under the continuous curve (sla = 45 s) they scored — (mean). Raw latency
   values are in the Langfuse trace comments and `--output-json` dump for
   re-computation.
4. **Comparison questions with expected refusals (gq-055, gq-057):** —
5. **Highest-impact prompt change for v2:** —

---

### Sign-off

| Role | Name | Date | Notes |
|------|------|------|-------|
| Layer 1 runner | — | — | Ran `qa_eval.py --prompt-version v1-baseline` |
| Layer 2 scorer (geographic) | Bryan | — | Manual scores for gq-041 – gq-050 |
| Layer 2 scorer (comparison) | Emilio | — | Manual scores for gq-051 – gq-060 |
| Baseline editor | — | — | Wrote this section |

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
