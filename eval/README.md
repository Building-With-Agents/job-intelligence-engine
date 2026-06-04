# Eval harnesses

## Golden-question Q&A eval (`qa_eval.py`)

Runs the production analytics Q&A path over [`qa_golden_questions.json`](qa_golden_questions.json), computes four numeric scores per item (`intent_accuracy`, `evidence_citation`, `confidence_self_consistency`, `latency_sla`), and optionally records results in Langfuse as a dataset run. **`intent_accuracy` is binary** (1.0 exact match / 0.0 else; JIE #261); use the printed intent confusion matrix to inspect near-miss routing. A fifth metric (`answerability`) is emitted separately for data-backed intents — see the subsection below.

### Answerability (5th metric, JIE #247)

`answerability` surfaces the gap between *classifier correctness* and *data availability*. It is scored **only** for golden items whose expected intent is marked data-backed in `eval/qa_scoring.py::INTENT_TO_DATA_BACKED`; intents not in the data-backed set (today: `trend`, `role_evolution`, `emergence`, `disruption` — temporal, awaiting `posted_date` + time-series aggregates) are **skipped** (`answerability = None`, no Langfuse `Evaluation` emitted).

| Condition | `answerability` |
|-----------|-----------------|
| Expected intent not data-backed in harness map (or `data_backed: false` in golden) | `None` (skipped) |
| Data-backed intent, pipeline error or no response | `None` (excluded, not 0.0) |
| Data-backed intent, `row_count_returned == 0` (and not `zero_rows_is_correct`) | `0.0` |
| Data-backed intent, `row_count_returned > 0`  | `1.0` |

Optional golden fields (JIE #269): `data_backed` (overrides the harness map), `expected_min_rows`, `zero_rows_is_correct`, `refusal_appropriate`. A sixth automated score, `correct_refusal` (1.0/0.0, or `None` for data-backed / no response), applies to intent-only items only and is not part of the four-metric `composite_score` mean. Run output includes `refusal_correctness_summary` (mean over the intent-only scored cohort) and JIE #268 `subcomposites` (prompt/classification/pipeline/safety, geometric mean, and `gated` when `QA_EVAL_ANSWERABILITY_GATE_THRESHOLD` is breached; `prompt_quality` proxies `evidence_citation` until #265).

`row_count_returned` is a new field on `AnalyticsQueryResponse` populated by the ORM path in `analytics.query_engine.routing.run_analytics_qna`.

**Not** part of the baseline composite (`composite_score` remains the mean of the four core metrics). Temporal intents fail answerability by design pre-`posted_date`; including them in the composite would drag the headline without reflecting classifier or prompt quality. Revisit weights in v2. Flip `INTENT_TO_DATA_BACKED` values to `True` as pipeline capabilities land — no coordination with Pair B required.

Reported separately in both local and Langfuse flows:

- Per-item: `answerability` on the item `scores` dict (local JSON) and as an `Evaluation(name="answerability")` on the Langfuse trace (only when scored).
- Run-level: `answerability_summary` with `mean`, `n_data_backed`, `n_intent_only_skipped`, `pass_rate` in the JSON output, plus `mean_answerability` as a run-level Langfuse `Evaluation` with comment `n=<scored> intent_only_skipped=<skipped> pass_rate=<float>`.

### Prerequisites

- Repo root, venv activated, `pip install -r requirements.txt`.
- Database reachable with aggregates populated (see Week 8 runbook) when using the default **in-process** path (`run_analytics_qna`).
- Azure / LLM env vars as for normal analytics (`LLM_DEFAULT`, `LLM_SYNTHESIS`, etc.).
- For Langfuse upload + dataset runs: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_BASE_URL` in `.env`.
- Upload the golden corpus once: `python scripts/upload_qa_dataset.py` (dataset **LaborPulse Golden Questions**).

### Full baseline (dataset run)

Uses the hosted Langfuse dataset (no `--limit`) so each item links to the uploaded dataset and the run name matches `--prompt-version`:

```powershell
python -m eval.qa_eval --prompt-version v1-baseline
```

> Corpus is 9 intents × 10 golden questions. The full baseline requires all rubrics authored; rows with empty `must_include` will distort `evidence_citation`.

### Pair C geographic + comparison cohort (`--cohort pair-c-geo-comp`)

Expands to **`gq-041` … `gq-060`** (20 golden rows). Optional **`--golden-ids id1,id2,...`** trims to an explicit list; when that list is **non-empty** after parsing, it **overrides** `--cohort` and **`--limit`**. Filtered questions always run in **lexicographic order by `id`**. Run summaries (`--json` / `--output-json`) include per-item **`composite`** (same definition as `eval.qa_scoring.composite_score`) and run-level **`composite_mean`**, **`composite_p25`**, **`composite_p25_method`**.

### Smoke (3 questions, no Langfuse)

Runs locally without calling Langfuse (no keys required):

```powershell
python -m eval.qa_eval --prompt-version v1-baseline --limit 3 --dry-run
```

### Smoke with Langfuse traces (no pre-uploaded dataset)

Uses JSON from disk with `run_experiment` (traces + scores; not the same as a full hosted-dataset run):

```powershell
python -m eval.qa_eval --prompt-version v1-smoke --limit 3 --local-experiment-only
```

### HTTP analytics API

Matches deployed `POST /analytics/query` behavior (server must be up):

```powershell
python -m eval.qa_eval --prompt-version v1-baseline --use-http --analytics-base-url http://127.0.0.1:8000
```

### Environment

| Variable | Role |
|----------|------|
| `QA_EVAL_LATENCY_SLA_SECONDS` | Latency SLA for `latency_sla` score (default **15**; JIE #270 tightened from 45s). Catastrophic latencies > SLA × 10 return `None` (excluded). |
| `QA_EVAL_ANSWERABILITY_GATE_THRESHOLD` | Float 0–1. When `mean_answerability` falls below this on a run, `overall_composite_status = "limited"` is emitted (default 0.40; JIE #268). |
| `ANALYTICS_QUERY_BASE_URL` | Base URL when `--use-http` (default `http://127.0.0.1:8000`). |
| `ANALYTICS_QUERY_X_TENANT_ID` | `X-Tenant-Id` for `POST /analytics/query` (default `borderplex`). Same as `scripts/smoke/smoke_issue197.py`. |
| `ANALYTICS_QUERY_X_USER_EMAIL` | `X-User-Email` (default `smoke@thewaifinder.com`). |
| `ANALYTICS_QUERY_X_API_KEY` | When set, sent as `X-API-Key`. Required when the API is configured with `JIE_API_KEYS` (see `scripts/run_analytics_api.py` curl). |

Per-item `X-Request-Id` is the harness `correlation_id` (not env-configured).

### Artifacts

- `--output-json path.json` writes per-item scores (and trace IDs when Langfuse is used).
- `--json` prints the same run summary as JSON to **stdout** and skips the human-readable console report (use for piping, `Tee-Object`, or redirecting to a file). Combine with `--output-json` to write the file and still emit JSON on stdout; file-write notices go to stderr so stdout stays valid JSON.

---

## v2 scorer architecture (JIE #260–#271)

All scoring changes were landed for the Week 10 harness redesign. The sections below describe the current behaviour.

### None semantics — infrastructure vs quality failures (JIE #263)

Content metrics (`intent_accuracy`, `evidence_citation`, `confidence_self_consistency`) return `None`
rather than `0.0` for infrastructure failures. Zeroing them conflates two orthogonal axes — pipeline
health vs answer quality — which would prevent pairs from distinguishing "fix the prompt" from "fix
the infrastructure."

| Failure class | Score | Rationale |
|---|---|---|
| `pipeline_error` / timeout / Azure 500 | `None` | Infrastructure failure; not gradable |
| `sql_execution_error_detail` set | `None` | Infrastructure failure on evidence path |
| Empty answer (contract violation) | `None` | Pipeline output contract broken |
| Committed answer with no evidence | `0.0` | Real quality failure; keeps signal |
| `latency_sla` catastrophic (> SLA × 10) | `None` | Infrastructure event, not quality signal |
| `latency_sla` all other | float 0–1 | Always computable from wall-clock time |

Run-level means are computed over the **scorable pool** (non-`None` values only). Per-metric
`n_scored` and `n_excluded` appear in every run summary and Langfuse mean comment.

### Binary intent accuracy (JIE #261)

`intent_accuracy` is a binary exact-match: 1.0 if `classified_intent == expected_intent`
(case/whitespace normalised), 0.0 otherwise. The old `0.5` partial-credit ladder backed by
`RELATED_INTENTS` is retired; `RELATED_INTENTS_OFFLINE` remains for confusion-matrix analysis only.

### Refusal scoring redesign (JIE #260)

`score_evidence_citation` now evaluates the rubric (`must_include`, `must_not_include`) against
refusal text rather than bypassing it. Refusals are further penalised when the expected intent is
data-backed (the pipeline should have committed a SQL-backed answer):

| Case | Score formula |
|---|---|
| Intent-only refusal | `rubric_score` (must_include coverage − penalty) |
| Data-backed refusal | `rubric_score × 0.35` (strong penalty for wrongful refusal) |

The `len(ans) > 40` length floor is removed. The `refused-with-evidence → 0.85` shortcut is removed.

### Component scores (JIE #265 phase 1)

`score_evidence_citation` returns four values: `(combined, comment, must_include_recall, evidence_overlap)`.
The last two are emitted as separate Langfuse `Evaluation` entries (`must_include_recall`,
`evidence_overlap`) and included in the durable JSON `scores` block. When `combined` is `None`
(infrastructure exclusion), both components are `None`.

### correct_refusal metric (JIE #269)

A sixth automated score, `correct_refusal` (1.0 / 0.0, or `None`), applies to intent-only
(non–data-backed) items. It measures whether the pipeline made the right commit-vs-refuse decision.

| Item type | `correct_refusal` |
|---|---|
| Data-backed expected intent | `None` (use `answerability` + `evidence_citation` instead) |
| Infrastructure failure (no response) | `None` (excluded) |
| Intent-only, refused | 0.0 by default; 1.0 if `refusal_appropriate: true` in golden |
| Intent-only, committed answer | 1.0 by default; 0.0 if `refusal_appropriate: false` in golden |

Optional golden fields: `data_backed` (bool), `expected_min_rows` (int), `zero_rows_is_correct` (bool),
`refusal_appropriate` (bool).

`answerability` (data-backed items) and `correct_refusal` (intent-only) together cover all 90
golden items. Run output includes `refusal_correctness_summary`.

### Sub-composites and geometric mean (JIE #268)

`run_subcomposites_and_gates` computes four sub-composites and an overall geometric mean:

| Sub-composite | Drives | Proxy until |
|---|---|---|
| `prompt_quality_composite` | `evidence_citation` mean | #265 full decomposition |
| `classification_composite` | `intent_accuracy` mean | — |
| `pipeline_health_composite` | `answerability`, `latency_sla`, `correct_refusal` (weighted) | — |
| `safety_composite` | `confidence_self_consistency`, `confidence_in_expected_range` | Layer 2 `no_hallucination_rate` |
| `overall_geometric_composite` | geometric mean of the four sub-composites | — |

The geometric mean penalises any single tanked axis. An arithmetic mean would paper it over.

When `mean_answerability < QA_EVAL_ANSWERABILITY_GATE_THRESHOLD`, the run emits
`gated: true` and `gate_message` to force reader awareness of coverage gaps.

### Per-stage Langfuse observations (JIE #258)

Each pipeline LLM call produces its own `@observe(as_type="generation")` child observation in
Langfuse, allowing per-stage drill-down in the trace tree:

| Observation name | Stage | LLM tier |
|---|---|---|
| `intent_classification` | Intent routing | `LLM_DEFAULT` (Haiku-class) |
| `sql_generation` | Text-to-SQL | `LLM_SYNTHESIS` (Sonnet-class) |
| `synthesis` | Answer synthesis | `LLM_SYNTHESIS` |
| `follow_up_generation` | Follow-up questions | `LLM_DEFAULT` |

Token usage and model name propagate via `report_langfuse_usage` in
`analytics/query_engine/langfuse_utils.py`. Cost attribution requires model registry population
in Langfuse (admin task, JIE #259).

### Per-intent score breakdown (JIE #256)

```powershell
python scripts/compute_per_intent_means.py
python scripts/compute_per_intent_means.py eval/runs/qa-v2-scorer-redesign.json
```

Prints a table of per-intent means (n + 6 score columns) from a durable run JSON. Flat trace
metadata (`intent`, `difficulty`, `gq_id`) is also set on every Langfuse trace so the Experiment
detail view supports per-intent filtering.

### Latency tail aggregates (JIE #270)

The Langfuse run-level evaluators emit five latency statistics alongside `mean_latency_sla`:

- `mean_latency_seconds`, `p50_latency_seconds`, `p95_latency_seconds`, `p99_latency_seconds` — raw wall-clock seconds
- `p95_latency_sla_score` — `min(1, SLA / p95)` for iteration signal at the tail

`catastrophic_excluded` in each comment counts items excluded from `latency_sla` scoring.

---

### Extraction eval

See [`run_extraction_eval.py`](run_extraction_eval.py) for the skills-extraction harness.

---

## Layer 2 — manual scoring integration (JIE #271)

Human annotators score three metrics in the Langfuse UI per trace:

| Metric | What it measures |
|--------|-----------------|
| `correctness` | Is the answer factually correct? (0–1) |
| `decision_relevance` | Does the answer serve the stakeholder's decision need? (0–1) |
| `followup_quality` | Are the follow-up questions useful and well-formed? (0–1) |

Cross-pair review assigns two humans to every item (owner + cross-pair reviewer). Their
scores are stored separately in Langfuse. Three scripts process this data post-scoring.

### Step 1 — Inter-rater reliability + aggregate emission

```powershell
python scripts/compute_layer2_irr.py --run-name v1-baseline
```

Reads human scores from Langfuse, computes per-metric IRR (Cohen's kappa, Pearson r,
disagreement distribution), aggregates multi-annotator scores using arithmetic mean,
and emits `*_aggregate` scores back to Langfuse.  Writes `eval/qa_irr_report.md`.

Optional flags:

| Flag | Purpose |
|------|---------|
| `--dry-run` | Compute and print without emitting Langfuse scores |
| `--json-output path.json` | Write machine-readable summary for downstream use |
| `--automated-scores-json path.json` | Provide `qa_eval --json` output for ECE computation |
| `--dataset-name NAME` | Override dataset name (default: `LaborPulse Golden Questions`) |

Run-level Langfuse scores emitted: `mean_human_correctness_composite`, `mean_confidence_ece`
(ECE only when `--automated-scores-json` provides confidence scores).

### Step 2 — Layer 1 vs Layer 2 divergence report

```powershell
# Requires qa_eval --json output and Layer 2 aggregate scores from Step 1
python scripts/compute_automated_human_divergence.py \
    --automated-json eval/runs/v1-baseline-scores.json \
    --run-name v1-baseline
```

Compares automated `evidence_citation` against human `correctness_aggregate`.
Sorts items by `|automated − human|` and classifies direction:

- **high-auto-low-human**: likely confident hallucination → priority prompt iteration target
- **high-human-low-auto**: likely semantic paraphrase → rubric loosening candidate

Writes `eval/qa_divergence_report.md`.  Use `--human-json path.json` (IRR `--json-output`)
to avoid a second Langfuse round-trip.

### Step 3 — Comment audit

```powershell
python scripts/audit_layer2_comments.py --run-name v1-baseline
```

Flags any human score below `--threshold` (default 0.7) that has an empty comment.
Exits with code 1 when violations exist (CI signal).  Use `--output path.md` for a
full markdown report.

### New scoring functions in `qa_scoring.py`

| Function | Description |
|----------|-------------|
| `aggregate_human_scores(scores)` | Arithmetic mean of annotator scores; warns on spread > 0.3 |
| `run_ece_from_correctness(confs, correctness)` | Expected Calibration Error (lower is better) |
| `score_confidence_correctness_alignment(confidence, correctness)` | `1 − \|conf − correctness\|`; None when Layer 2 pending |
| `compute_no_hallucination_rate(items)` | Fraction of labeled items that are not confidently hallucinating |
| `human_correctness_composite(correctness, dr, fq)` | Weighted mean 0.5/0.3/0.2 |

`subcomposites_from_means` accepts an optional `no_hallucination_rate` argument.
When provided, it is blended into `safety_composite` at 20 % weight alongside the
automated self-consistency signal.

### Environment

No additional env vars are required beyond the Langfuse credentials already used by
`qa_eval.py` (`LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_BASE_URL`).
