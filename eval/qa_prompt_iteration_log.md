# Q&A Eval Harness — Prompt Iteration Log

Tracks every dataset run against the **LaborPulse Golden Questions** corpus.
One row per `--prompt-version` in the version history; full per-run analysis
lives in `eval/runs/findings{version}.md` (findings-per-milestone pattern,
adopted from v2 onward per IMP-030 / JIE #287).

> **Canonical source of truth:** Langfuse dataset runs. Scores in Langfuse are
> authoritative if they disagree with a row here.

---

## Version history

| Version | Date | Author | Scorer | Change summary | Composite | Answerability | Findings |
|---------|------|--------|--------|----------------|:---------:|:-------------:|----------|
| `v1-baseline` | 2026-04-23 | Bryan + Emilio | v1 (partial-credit intent, lenient refusal) | Initial baseline — 90 golden Qs, unmodified prompts, in-process pipeline | **0.875** | 0.260 (n=50) | `eval/runs/qa-v1-baseline.json` |
| `v2-scorer-redesign` | 2026-04-24 | Bryan + Emilio | v2 (binary intent, geometric composite, None exclusions) | Scorer redesign (PR #278) — binary intent, stronger refusal penalty, `None` for infra failures; run against empty aggregate tables (pre-#291) | **0.510** geo | 0.060 (n=50) | [`eval/runs/findingsv2.md`](findingsv2.md) |
| `v2.1-post-seed-fix` | 2026-04-27 | Bryan | v2 (same scorer as v2) | Re-run post-PR #291 aggregate table seed fix; confirms data-seed was sole cause of 0.06 answerability collapse | — | **0.340** (n=50) | [`eval/runs/findingsv2.1.md`](findingsv2.1.md) |

> **v2 vs v2.1 note:** scorer logic did not change between v2 and v2.1.
> The only difference is that aggregate tables were empty during the v2 run
> (post-#285 re-export) and populated during v2.1 (post-#291 refresh).
> The composite field for v2.1 reflects individual metric means rather than
> the geometric composite gate; see `findingsv2.1.md` for the full breakdown.

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

**Status:** Deviation accepted for v1-baseline. Revisit once
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
re-run in-process (see v1-baseline row in version history above).

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

**Resolution:** Fixed in JIE #255 (PR #207, merged). `execute_qa_item`
now sends the correct headers (`X-Tenant-Id`, `X-User-Email`,
`X-Request-Id`) and renames `correlation_id` → `conversation_id` in
the request body. Integration tests added to prevent silent regression.

**Status:** Resolved.

---

## How to add a future run

1. Run `eval/qa_eval.py --prompt-version <semantic-tag> --output-json eval/runs/<tag>.json`.
2. Add a row to the **Version history** table with a link to the findings file.
3. Create `eval/runs/findings<tag>.md` using the findings-per-milestone template
   (see `findingsv2.md` as reference): hypothesis tested, three-way scorecard,
   result, what changed, remaining gaps, next steps.
4. Commit the findings doc alongside any prompt change (or immediately after the
   run if no prompt changed).

**Comparison guidance:** Look at per-intent score distributions, not only overall
means. A run with a higher overall mean but a collapsing p25 has gotten *worse*
on hard questions. At n ≈ 90, a 2-point composite delta is within noise.

---

## Changelog

| Date | Who | Change |
|------|-----|--------|
| 2026-04-22 | Bryan | Created skeleton; documented DEV-001–DEV-003; v1-baseline section stubbed |
| 2026-04-23 | Bryan + Emilio | v1-baseline run complete (composite 0.875, answerability 0.260); added DEV-004 for `--use-http` regression |
| 2026-04-27 | Bryan | Restructured per JIE #287: per-run analysis moved to `eval/runs/findings*.md` pattern; log now holds version history table + DEV registry only. Added v2 and v2.1 rows; updated DEV-004 status to Resolved |
