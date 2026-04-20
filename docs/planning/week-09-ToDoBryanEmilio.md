# WEEK-09 — Dev 1 + Dev 2 (Eval Harness + Baseline)

| | |
|---|---|
| **Goal** | `scripts/upload_qa_dataset.py`, `eval/qa_eval.py`, Langfuse run `v1-baseline`, `eval/qa_prompt_iteration_log.md` |
| **Reading** | [IMP-030 — Langfuse dataset runs](https://github.com/Building-With-Agents/curriculum/blob/main/lesson-framework/week-09/readings/IMP-030-eval-harness-langfuse-dataset-runs-bryan-emilio-reading.md) · [Week 9 runbook](https://github.com/Building-With-Agents/curriculum/blob/main/lesson-framework/week-09/WEEK-09-eval-harness-baseline-bryan-emilio-runbook.md) |

---

## Ownership (avoid merge conflicts)

| Who | Owns |
|-----|------|
| **Dev 1** | `scripts/upload_qa_dataset.py` until it is merged. Open **PR 1** for this file only. |
| **Dev 2** | `eval/qa_eval.py` until it is merged. Open **PR 2** for this file only. |
| **Either (pick one in chat)** | `eval/qa_prompt_iteration_log.md` baseline section — **one editor** so you do not overwrite each other. |
| **Neither edits blindly** | `eval/qa_golden_questions.json` — read-only unless you agree on a single coordinated change. |

Merge order: **uploader PR → eval PR → baseline doc**. Branch from current `development` before each slice.

---

## What Dev 1 does

You put the **80 golden questions into Langfuse once** as dataset **LaborPulse Golden Questions**: each item is the question text plus metadata (`expected_intent`, `must_include`, `must_not_include`, `ideal_answer_summary`). Follow the existing pattern in `scripts/upload_langfuse_dataset.py` and `common/observability/langfuse.py`. After upload, sanity-check items in the Langfuse UI. You review Dev 2’s eval PR; you help run or monitor the full **`v1-baseline`** eval when the API is up. You complete **Pair C manual scoring** (your 20 geographic/comparison questions) in Langfuse when Layer 2 is ready.

---

## What Dev 2 does

You build the **automated scorer**: `eval/qa_eval.py` with **required** `--prompt-version` (e.g. `v1-baseline`), loop all 80 rows from JSON, call `POST /analytics/query`, read timing and trace id, compute the four **NUMERIC** scores (`evidence_citation`, `confidence_flags`, `intent_accuracy`, `latency_sla`), attach scores with `langfuse.score`, link each run to the right dataset item via Langfuse’s **dataset run** flow, and **`flush()`** on exit. On errors, score **0.0** everywhere and record why — do not drop questions. Print a useful console summary (per-intent, worst items). You review Dev 1’s upload PR. Optional: local JSON backup of scores if Langfuse is flaky. You share Pair C manual scoring with Dev 1 (agree who scores which IDs so you do not duplicate blindly).

---

## Research questions (helpful while building)

**Gemini Deep Research** — broad synthesis, good when you want tradeoffs and “what teams do,” not a single doc page.

- How do you separate **signal from noise** on ~80 questions (overall vs per-intent; min/p25/p50/p75/max vs a single average)?
- How do teams combine **automated structural scores** with **human correctness** without trusting regex too much?
- What **evidence citation** patterns in free text are robust to **paraphrase** (counts, eras, table names, percentages)?
- If observability is **down mid-run**, what **lightweight backup** (e.g. local JSON of trace id + scores) is worth it?

**Normal research (docs + repo)** — SDK facts and this codebase.

- Langfuse: datasets, items, **runs**, traces, `dataset_item_id`, `score()`, `flush()` — [data model](https://langfuse.com/docs/evaluation/experiments/data-model), Python SDK.
- Repo: `scripts/upload_langfuse_dataset.py`, `common/observability/langfuse.py`, `POST /analytics/query` response shape (trace id, `confidence`, classified intent).
- `eval/qa_golden_questions.json`: `expected_intent`, `difficulty: hard`, multi-intent handling for **intent_accuracy**.
- `docs/guides/local-langfuse-setup.md` for env and UI checks.

---

## Task list

### Dev 1

- [ ] Read IMP-030 + Week 9 runbook
- [ ] Implement `scripts/upload_qa_dataset.py` (80 items, metadata fields above)
- [ ] Upload to Langfuse; verify a sample of items in UI
- [ ] Open **PR 1** (upload script only); get review from Dev 2; merge
- [ ] After eval exists: help run **`eval/qa_eval.py --prompt-version v1-baseline`** (or support Dev 2)
- [ ] Confirm in Langfuse: run **`v1-baseline`** links 80 items → traces; scores on traces
- [ ] Enter **Layer 2** manual scores for Pair C’s 20 questions (coordinate split with Dev 2)

### Dev 2

- [ ] Read IMP-030 + Week 9 runbook (focus: trace ↔ dataset item ↔ run)
- [ ] Implement `eval/qa_eval.py` (required `--prompt-version`, four scores, dataset run linking, `flush`, error → 0.0 + comment)
- [ ] Implement **intent_accuracy** matrix + hard-question rules in code
- [ ] Implement **evidence_citation** with multiple patterns (not one brittle string)
- [ ] Console summary: per-intent, overall, worst questions
- [ ] Open **PR 2** (`qa_eval.py` only); get review from Dev 1; merge after PR 1
- [ ] Smoke-test a few questions, then full **80-question** baseline run
- [ ] Optional: JSON backup of `(trace_id, scores)` per run
- [ ] Enter **Layer 2** manual scores for Pair C (coordinate with Dev 1)

### Together

- [ ] Pick **one person** to write `eval/qa_prompt_iteration_log.md` baseline (overall + per-intent + per-score + top 5 worst + manual averages + observations)
- [ ] Notify pairs A, B, D to complete manual scoring in Langfuse
- [ ] Quick pass on Langfuse dashboard when manual scores are in

### Done when (runbook)

- [ ] 80/80 questions ran with four automated scores each (failures scored, not skipped)
- [ ] Each trace has `evidence_citation`, `confidence_flags`, `intent_accuracy`, `latency_sla`
- [ ] Dataset run **`v1-baseline`** shows all items linked
- [ ] Baseline documented in `qa_prompt_iteration_log.md`
- [ ] Repeatability: a new `--prompt-version` creates a **new** run for comparison

---

*Week 9 — Dev 1 + Dev 2.*
