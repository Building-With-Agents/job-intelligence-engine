# Eval harnesses

## Golden-question Q&A eval (`qa_eval.py`)

Runs the production analytics Q&A path over [`qa_golden_questions.json`](qa_golden_questions.json), computes four numeric scores per item (`intent_accuracy`, `evidence_citation`, `confidence_flags`, `latency_sla`), and optionally records results in Langfuse as a dataset run.

### Prerequisites

- Repo root, venv activated, `pip install -r requirements.txt`.
- Database reachable with aggregates populated (see Week 8 runbook) when using the default **in-process** path (`run_analytics_qna`).
- Azure / LLM env vars as for normal analytics (`LLM_DEFAULT`, `LLM_SYNTHESIS`, etc.).
- For Langfuse upload + dataset runs: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_BASE_URL` in `.env`.
- Upload the golden corpus once: `python scripts/upload_qa_dataset.py` (dataset **LaborPulse Golden Questions**).

### Full 80-question baseline (dataset run)

Uses the hosted Langfuse dataset (no `--limit`) so each item links to the uploaded dataset and the run name matches `--prompt-version`:

```powershell
python -m eval.qa_eval --prompt-version v1-baseline
```

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

### Artifacts

- `--output-json path.json` writes per-item scores (and trace IDs when Langfuse is used).
- `--json` prints the same run summary as JSON to **stdout** and skips the human-readable console report (use for piping, `Tee-Object`, or redirecting to a file). Combine with `--output-json` to write the file and still emit JSON on stdout; file-write notices go to stderr so stdout stays valid JSON.

### Extraction eval

See [`run_extraction_eval.py`](run_extraction_eval.py) for the skills-extraction harness.
