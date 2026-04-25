# Q&A eval harness — findings (Week 9 / v1-baseline)

Synthesis of the 2026-04-23 team thread (Option A vs B, v1-baseline, Langfuse), the `eval/qa_eval.py` harness, and the logged run in `eval/qa_prompt_iteration_log.md`. Use this for handoff, release notes, or follow-up work tickets.

## Executive summary

- **v1-baseline is complete** on the shared Langfuse project: 90/90 items, in-process path, composite **0.875** (four-metric mean; `answerability` reported separately).
- The **first Langfuse run** (HTTP-based attempt) was **discarded** as invalid; a **second run** is the official baseline. Team lead **Option A** was to drop `--use-http` and re-run in-process; **Option B** (fix HTTP in `execute_qa_item`, align with JIE #222) is a **non-blocking** follow-up tracked as **[JIE #255](https://github.com/Building-With-Agents/job-intelligence-engine/issues/255)**.
- **Langfuse “empty score config”** behavior on the shared cloud org was unblocked with **`scripts/setup_qa_score_configs.py`** (idempotent registration of 5 automated + 3 manual score configs). Landed in commit `9f42d7270f0943237dcbefd33d0399c0a338bcd6` (*feat(eval): add setup_qa_score_configs.py…*).

## Decision: in-process (Option A) for v1-baseline

**Rationale (science):** the default harness path calls `run_analytics_qna(session, question, correlation_id)` — the same function the `POST /analytics/query` route invokes after request validation. Layer 1 metrics (`intent_accuracy`, `evidence_citation`, `confidence_flags`, `latency_sla`, and `answerability` when emitted) are computed from the same response `dict` whether the harness uses DB session + routing or a successful HTTP call. The HTTP wire (headers, FastAPI validation) is **out of scope** for the v1 baseline, so skipping it does not change what is being measured.

**Command for official baseline:**

```text
python -m eval.qa_eval --prompt-version v1-baseline
```

Omit `--use-http` until the follow-up hardening in JIE #255 is merged.

## Decision: Option B (HTTP path) = follow-up, not blocker

**Problem:** `execute_qa_item`’s HTTP branch posts JSON with `question` and `correlation_id` only, with **no** LaborPulse headers. After JIE #222, the analytics API requires `X-Tenant-Id`, `X-User-Email`, `X-Request-Id`, and (unless explicitly allowed) `X-API-Key`, and the body field may differ from the legacy `correlation_id` shape. See **`eval/qa_prompt_iteration_log.md` → DEV-004** and `dashboard/analytics_query_client.py` for the header pattern production clients use.

**Effect:** with `--use-http`, every item returned `http_400`, so that path is **broken** until the harness is aligned. **Scope is limited to the HTTP branch**; in-process scoring and metrics logic were not wrong.

**Tracking:** [job-intelligence-engine#255](https://github.com/Building-With-Agents/job-intelligence-engine/issues/255) — include header/body fix in `execute_qa_item` and a small smoke (e.g. `--use-http --limit 1 --dry-run`) so the next contract change cannot silently regress the harness.

## Langfuse runs

| Event | Run ID (Langfuse) | Note |
|--------|-------------------|------|
| **Delete (polluted)** | `53e7558f-debe-4dcf-9553-997ce82c4a1e` | Baseline attempt while `--use-http` returned 400 for all items — removed so Week 10 comparisons stay clean. |
| **Authoritative v1-baseline** | `27d1ac84-4317-44db-b40f-772c24de5826` | 90/90, in-process. Durable JSON: `eval/runs/qa-v1-baseline.json`. |

Dataset: **LaborPulse Golden Questions** on `langfuse.watechcoalition.org`.

## v1-baseline headline results (Layer 1)

All detail (per-intent tables, worst-N, sign-off) lives in **`eval/qa_prompt_iteration_log.md`** under **v1-baseline**. High level:

| Item | Value |
|------|--------|
| **Composite (4-metric)** | **0.875** |
| **intent_accuracy** | **0.811** (e.g. 63 exact, 20 related, 7 mismatch) |
| **evidence_citation** | **0.691** (heavily influenced by refusal / no-evidence template scoring on many items) |
| **confidence_flags** | **1.000** |
| **latency_sla** | **1.000** (SLA 45 s; see log for p50 / p95 / long-tail note) |
| **answerability** (separate, n=50 scored) | **0.260**; 40 intent-only items **skipped** by design until temporal / data-backed pipeline work lands |

**Dominant product insight:** *geographic → employer* classifier confusion accounts for a large share of the seven intent mismatches; improving that alone is estimated to lift composite on the order of **~0.875 → ~0.889** without new data (see team message and per-intent breakdown in the log).

**Answerability v1 policy:** not part of the composite; `INTENT_TO_DATA_BACKED` in `eval/qa_scoring.py` governs which golden rows get `answerability` until `posted_date` and related capabilities flip more intents to scored.

## Langfuse score configs (manual scoring unblocked)

**Observed issue:** on the org’s Langfuse Cloud settings, **score-config auto-creation is disabled**, so `dataset.run_experiment` can attach the five automated scores to traces while **Settings → Scores** stays an **empty registry** — the **“Add score”** UI reads the registry, so **manual** scores (`correctness`, `decision_relevance`, `followup_quality`) could not be added.

**Fix:** `scripts/setup_qa_score_configs.py` registers all **8** configs (5 automated + 3 manual) as **NUMERIC 0.0–1.0** with rubric-oriented descriptions, idempotently. Treat it like `scripts/upload_qa_dataset.py` for new projects or new Langfuse instances.

**Team direction:** keep the script in-repo for reproducibility and as the **source of truth** for what each score means. Document cross-link from the Week 9 **Human annotation** / annotation appendix in `reading-langfuse-scoring-tutorial.md` when that doc is present (see script docstring and Week 9 planning).

**Commit (current repo):** `9f42d7270f0943237dcbefd33d0399c0a338bcd6`.

## Harness map (this repo)

| Path | Role |
|------|------|
| `eval/qa_eval.py` | Main harness: golden merge, `execute_qa_item`, evaluators, Langfuse dataset run / local experiment. |
| `eval/qa_scoring.py` | Composite, per-item scores, confusion, `INTENT_TO_DATA_BACKED` for `answerability`. |
| `eval/qa_golden_questions.json` | Golden corpus (9 intents × 10 questions). |
| `eval/README.md` | Operator commands, flags, `answerability` contract. |
| `eval/qa_prompt_iteration_log.md` | v1-baseline run log, DEV-004 (`--use-http` regression), sign-off. |
| `eval/runs/qa-v1-baseline.json` | Durable per-item run output. |
| `scripts/setup_qa_score_configs.py` | One-shot Langfuse score config registration. |
| `scripts/upload_qa_dataset.py` | One-shot dataset upload. |

## Follow-ups (ordered by impact / dependency)

1. **JIE #255** — Repair `--use-http` in `execute_qa_item` + contract tests/smoke.  
2. **Classifier** — Reduce geographic ↔ employer routing errors (largest v1 → v2 lever for composite).  
3. **Data / pipeline** — `posted_date` and time-series work so more intents become data-backed; `evidence_citation` and `answerability` then reflect real quality rather than refusal templates.  
4. **Layer 2** — Manual scores in Langfuse (now unblocked by script) per Week 9 rubric.  
5. **Docs** — Ensure `reading-langfuse-scoring-tutorial.md` appendix references `setup_qa_score_configs.py` (per team lead ask).

## References

- GitHub issue: [eval/qa_eval.py --use-http missing required headers…](https://github.com/Building-With-Agents/job-intelligence-engine/issues/255)  
- In-repo: `eval/qa_prompt_iteration_log.md` (DEV-004, v1-baseline)  
- API contract: `analytics/api/routes.py` (`POST /analytics/query` headers, JIE #222)
