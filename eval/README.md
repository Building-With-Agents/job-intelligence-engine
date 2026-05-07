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
| `QA_EVAL_LATENCY_SLA_SECONDS` | Latency SLA for `latency_sla` score (default 45). |
| `ANALYTICS_QUERY_BASE_URL` | Base URL when `--use-http` (default `http://127.0.0.1:8000`). |
| `ANALYTICS_QUERY_X_TENANT_ID` | `X-Tenant-Id` for `POST /analytics/query` (default `borderplex`). Same as `scripts/smoke/smoke_issue197.py`. |
| `ANALYTICS_QUERY_X_USER_EMAIL` | `X-User-Email` (default `smoke@thewaifinder.com`). |
| `ANALYTICS_QUERY_X_API_KEY` | When set, sent as `X-API-Key`. Required when the API is configured with `JIE_API_KEYS` (see `scripts/run_analytics_api.py` curl). |

Per-item `X-Request-Id` is the harness `correlation_id` (not env-configured).

### Artifacts

- `--output-json path.json` writes per-item scores (and trace IDs when Langfuse is used).
- `--json` prints the same run summary as JSON to **stdout** and skips the human-readable console report (use for piping, `Tee-Object`, or redirecting to a file). Combine with `--output-json` to write the file and still emit JSON on stdout; file-write notices go to stderr so stdout stays valid JSON.

### Extraction eval

See [`run_extraction_eval.py`](run_extraction_eval.py) for the skills-extraction harness.
