# WEEK-09 — Bryan + Emilio (Eval harness + baseline)

| | |
|---|---|
| **Goal** | `scripts/upload_qa_dataset.py`, `eval/qa_eval.py`, Langfuse run `v1-baseline`, `eval/qa_prompt_iteration_log.md` |
| **Reading** | In-repo: [`docs/Week 9/IMP-030-eval-harness-langfuse-dataset-runs-bryan-emilio-reading.md`](../Week%209/IMP-030-eval-harness-langfuse-dataset-runs-bryan-emilio-reading.md) · [`docs/Week 9/WEEK-09-eval-harness-baseline-bryan-emilio-runbook.md`](../Week%209/WEEK-09-eval-harness-baseline-bryan-emilio-runbook.md) · [IMP-030 (curriculum)](https://github.com/Building-With-Agents/curriculum/blob/main/lesson-framework/week-09/readings/IMP-030-eval-harness-langfuse-dataset-runs-bryan-emilio-reading.md) · [Runbook (curriculum)](https://github.com/Building-With-Agents/curriculum/blob/main/lesson-framework/week-09/WEEK-09-eval-harness-baseline-bryan-emilio-runbook.md) |

---

## Pair C — golden questions (10 + 10)

Per Week 9 intent map ([`docs/Week 9/reading-jsis-query-reference.md`](../Week%209/reading-jsis-query-reference.md)):

| Who | Owns in `eval/qa_golden_questions.json` | Count |
|-----|------------------------------------------|------|
| **Bryan** | All questions with **`intent`: `geographic`** | **10** |
| **Emilio** | All questions with **`intent`: `comparison`** | **10** |

Each person authors their **10** rows (schema + process: [`docs/Week 9/reading-golden-questions-and-ground-truth.md`](../Week%209/reading-golden-questions-and-ground-truth.md)). Land them in **one coordinated update** to the shared file (commits on the pair branch) so IDs and ordering stay clean.

---

## One PR (pair branch → `development`)

Ship everything in **a single pull request**: shared branch (e.g. `week-09-eval-harness-pair-c`), both scripts, Pair C golden rows, and the baseline log when the run is done.

**Suggested commit order on that branch** (keeps integration sane):

1. Pair C **`eval/qa_golden_questions.json`** rows (10 + 10) if not already on `development`.
2. **`scripts/upload_qa_dataset.py`** (Bryan).
3. **`eval/qa_eval.py`** (Emilio) — after uploader exists so reviewers can read the full flow.
4. **`eval/qa_prompt_iteration_log.md`** baseline (one agreed commit, after `v1-baseline` run).

---

## Ownership — files (avoid merge conflicts)

| Who | Owns (edits; still one PR) |
|-----|----------------------------|
| **Bryan** | `scripts/upload_qa_dataset.py` — do not mix unrelated edits into `qa_eval.py`. |
| **Emilio** | `eval/qa_eval.py` — do not mix unrelated edits into `upload_qa_dataset.py`. |
| **Either (agree in chat)** | `eval/qa_prompt_iteration_log.md` baseline section — **one editor** at a time. |
| **Coordinated** | `eval/qa_golden_questions.json` — Pair C’s 20 rows (10 + 10); avoid simultaneous edits (split by `intent` / ID range, or one person commits the file per slice). |

Branch from current **`development`** once, push to the pair branch, open **one PR**, both of you review before merge.

---

## Work split

Rough balance: **Bryan** carries dataset upload + Langfuse verification + golden **geographic** block; **Emilio** carries the heavier **eval harness** implementation + execution smoke/full run + golden **comparison** block. **Manual Layer 2** and **baseline write-up** are split evenly (10 scores each; one baseline editor).

### Bryan

- Implement **`scripts/upload_qa_dataset.py`** on the pair branch — read `eval/qa_golden_questions.json`, create dataset **LaborPulse Golden Questions**, **80** items. Each item: question + metadata. Include stable **`id`** (e.g. `gq-042`) in metadata; map JSON field **`intent`** → metadata **`expected_intent`** (keep both if useful for debugging).
- Follow `scripts/upload_langfuse_dataset.py` + Langfuse v4 patterns; **`flush()`** at end of upload.
- **Author 10** golden rows: **`intent` = `geographic`**.
- Review **Emilio’s** commits on the same PR (especially `qa_eval.py`); help **run or watch** `eval/qa_eval.py --prompt-version v1-baseline` when API + Langfuse are up.
- In Langfuse: confirm dataset items and that run **`v1-baseline`** links **80** items → traces with four automated scores.
- **Layer 2 manual scores** in Langfuse: **only your 10** `geographic` questions (`correctness`, `decision_relevance`, `followup_quality`).

### Emilio

- Implement **`eval/qa_eval.py`** on the pair branch — required **`--prompt-version`**, loop **80** from JSON, call **`POST /analytics/query`**, timing + scores. Four **NUMERIC** scores: `evidence_citation`, `confidence_flags`, `intent_accuracy`, `latency_sla`. Prefer API fields for confidence behavior (**`confidence`**, **`confidence_flagged_low`**, **`confidence_explanation`**) where available; align **`intent_accuracy`** with whatever the response exposes for classified intent (extend API if needed for HTTP-based eval).
- Link traces to dataset items via Langfuse **dataset run** flow (v4: `get_dataset` → `item.run(...)` → score on trace); **`flush()`** on success and in **`finally`**.
- On pipeline/HTTP errors: score **0.0** on all four + **comment**; **do not skip** rows.
- Console summary: per-intent, overall, worst items (distributions, not only the mean).
- **Author 10** golden rows: **`intent` = `comparison`**.
- Review **Bryan’s** upload script on the same PR; **smoke-test** a few questions, then drive the full **80-question** baseline run (Bryan can co-pilot).
- Optional: local **JSON** backup of `(trace_id, scores)` per run if Langfuse is flaky.
- **Layer 2 manual scores** in Langfuse: **only your 10** `comparison` questions.

### Together

- Agree who **once** edits `eval/qa_prompt_iteration_log.md` for the baseline (overall + per-intent + per-score + top 5 worst + manual averages + observations).
- Notify pairs **A, B, D** to complete their Layer 2 manual scoring in Langfuse.
- Quick pass on Langfuse dashboard when all manual scores are in.

---

## Research questions (helpful while building)

**Gemini Deep Research** — broad synthesis, good when you want tradeoffs and “what teams do,” not a single doc page.

- How do you separate **signal from noise** on ~80 questions (overall vs per-intent; min/p25/p50/p75/max vs a single average)?
- How do teams combine **automated structural scores** with **human correctness** without trusting regex too much?
- What **evidence citation** patterns in free text are robust to **paraphrase** (counts, eras, table names, percentages)?
- If observability is **down mid-run**, what **lightweight backup** (e.g. local JSON of trace id + scores) is worth it?

**Normal research (docs + repo)** — SDK facts and this codebase.

- Langfuse: datasets, items, **runs**, traces, `dataset_item_id`, scoring API, **`flush()`** — [data model](https://langfuse.com/docs/evaluation/experiments/data-model), Python SDK.
- Repo: `scripts/upload_langfuse_dataset.py`, `common/observability/langfuse.py`, `analytics/api/schemas.py` (**`AnalyticsQueryResponse`**), `POST /analytics/query`.
- `eval/qa_golden_questions.json`: **`intent`** / **`expected_intent`** mapping, **`difficulty`: `hard`**, multi-intent rules for **intent_accuracy** (see IMP-030).
- `docs/guides/local-langfuse-setup.md` for env and UI checks.

---

## Task checklist

### Bryan

- [ ] Read IMP-030 + Week 9 runbook (+ golden-questions reading)
- [ ] Author **10** golden questions (`intent`: **`geographic`**); commit on pair branch (coordinate with Emilio on JSON)
- [ ] Implement `scripts/upload_qa_dataset.py` (80 items, metadata: `id`, `expected_intent`, `must_include`, `must_not_include`, `ideal_answer_summary`, …)
- [ ] Upload to Langfuse; verify sample items in UI
- [ ] Push commits to the **single pair PR**; request review from Emilio
- [ ] After `qa_eval.py` lands on the branch: help run **`eval/qa_eval.py --prompt-version v1-baseline`**
- [ ] Confirm run **`v1-baseline`**: 80 links + four automated scores per trace
- [ ] Langfuse **Layer 2**: manual scores for **your 10** `geographic` items only

### Emilio

- [ ] Read IMP-030 + Week 9 runbook (focus: trace ↔ dataset item ↔ run)
- [ ] Author **10** golden questions (`intent`: **`comparison`**); commit on pair branch (coordinate with Bryan on JSON)
- [ ] Implement `eval/qa_eval.py` (required `--prompt-version`, four scores, dataset run, `flush`, error → 0.0 + comment)
- [ ] **intent_accuracy** matrix + hard-question rules; **evidence_citation** multi-pattern (paraphrase-safe)
- [ ] Push commits to the **same pair PR**; request review from Bryan (after uploader is in a reviewable state)
- [ ] Smoke-test, then full **80-question** baseline run
- [ ] Optional: JSON backup `(trace_id, scores)` per run
- [ ] Langfuse **Layer 2**: manual scores for **your 10** `comparison` items only

### Together

- [ ] Open **one PR** from the pair branch when harness + goldens are ready; add baseline log commit after `v1-baseline` run; **both approve** before merge
- [ ] One owner for `eval/qa_prompt_iteration_log.md` baseline section
- [ ] Notify pairs A, B, D for Layer 2 manual scoring
- [ ] Langfuse dashboard review when manual scoring is complete

### Done when (runbook)

- [ ] 80/80 questions ran with four automated scores each (failures scored, not skipped)
- [ ] Each trace has `evidence_citation`, `confidence_flags`, `intent_accuracy`, `latency_sla`
- [ ] Dataset run **`v1-baseline`** shows all items linked
- [ ] Baseline documented in `qa_prompt_iteration_log.md`
- [ ] Repeatability: a new `--prompt-version` creates a **new** run for comparison
- [ ] Pair C: **10 + 10** golden questions present; **10 + 10** Layer 2 manual scores entered (no duplicate IDs)

---

*Week 9 — Bryan + Emilio (Pair C).*
