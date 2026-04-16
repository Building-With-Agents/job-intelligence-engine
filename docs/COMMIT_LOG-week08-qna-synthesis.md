# Commit log — Week 8 QnA synthesis + Streamlit import fix

**Branch:** `week-08/synthesis-citation`  
**Commit:** `26a848f` — `feat(analytics): QnA synthesis voice layer (#117) + Streamlit sys.path`  
**Tracking:** [GitHub #117](https://github.com/Building-With-Agents/job-intelligence-engine/issues/117), `.cursor/rules/analytics-qna-synthesis.mdc`

---

## Follow-up — grounding, guardrailed routing, Streamlit (#117)

| Area | Description |
|------|-------------|
| **Grounding** | `analytics/query_engine/grounding.py` — deterministic numeric verification against `EvidenceBundle`; `synthesis.py` applies verifier + one synthesis retry, then safe fallback; `prefix_period_coverage` ensures period appears in user-visible prose. |
| **Cost legs** | `analytics/query_engine/ledger_utils.py` — `append_leg_from_complete` shared by synthesis + routing. |
| **SQL guardrails** | `analytics/query_engine/sql_guardrails.py` — `validate_sql`, `extract_tables_referenced`; tests in `analytics/tests/test_sql_guardrails.py`. |
| **Routing** | `analytics/query_engine/routing.py` — `run_guardrailed_analytics_query` → intent (`classification`) + SQL (`analytics`) → validate → read-only execute → `run_analytics_qna`. |
| **Schema** | `QueryResultPayload.distinct_posting_count` optional; `evidence.py` volume uses it or **max** per-row counts when multiple rows. |
| **Streamlit** | `dashboard/pages_ask_the_data.py` + sidebar entry in `streamlit_app.py`. |
| **Demo** | `scripts/demo_qna_synthesis_metrics.py` patches `analytics.query_engine.synthesis.complete` (not `_invoke_qna_completion`). |
| **Tests** | `test_qna_grounding.py`, `test_qna_routing.py`; evidence test updates for max volume heuristic. |

### Follow-up — SQL execution error detail (Ask the Data debug)

**Commit:** `53b3e83` — `Ask Data debug` (on `week-08/synthesis-citation`)

When guardrailed SQL **executes** but PostgreSQL raises (e.g. `ProgrammingError`), operators and Streamlit users need the **driver/DB message**, not only `sql_execution_failed:ProgrammingError`.

| Area | Description |
|------|-------------|
| **Routing** | `analytics/query_engine/routing.py` — `_truncate_sql_execution_error` (single line, 400 chars, `exc` + `__cause__`); `log.warning("analytics_qna_sql_execution_failed", …, error_detail=…)`; `QueryResultPayload.sql_execution_error_detail`. |
| **Schemas** | `QueryResultPayload.sql_execution_error_detail`; `EvidenceBundle.sql_execution_error_detail`; `SynthesisResponse.sql_execution_error_detail` (API/UI echo; no user query text in this field). |
| **Evidence** | `build_evidence_bundle`: refusal reason appends `. PostgreSQL: {detail}` when detail is set. |
| **Synthesis** | Refused-path `SynthesisResponse` sets `sql_execution_error_detail` from the bundle. |
| **Streamlit** | `dashboard/pages_ask_the_data.py` — collapsible **Database error (debug)** with `st.code` when the field is present. |
| **Tests** | `test_routing_sql_execution_failure_surfaces_truncated_db_message`; `test_build_evidence_bundle_router_error_appends_postgres_detail`; router-errors test asserts `sql_execution_error_detail is None` when absent. |

**PR:** Reference **#117**, Week 8 runbook (`docs/Week 8/WEEK-08-synthesis-evidence-citation-bryan-emilio-runbook.md`), and `.cursor/rules/analytics-qna-synthesis.mdc`.

---

## Summary of changes

| Area | Description |
|------|-------------|
| **Voice layer** | `analytics/query_engine/synthesis.py` — `synthesize_answer`: refusal when `bundle.refuse_synthesis`; transparency flags from `analytics/query_engine/constants.py` only; echo citations and `period_coverage`; two LLM calls (main answer + follow-ups); merge inbound `CostLedger` and append `synthesis` / `follow_up` legs; USD via `common.llm_adapter` (`complete`, `compute_extraction_cost`); Azure OpenAI via lazy `common.llm_client.invoke_skills_llm`; safe response if main LLM fails. |
| **Orchestration** | `analytics/query_engine/qna.py` — `run_analytics_qna(query_result, *, cost_ledger=None)` runs `build_evidence_bundle` then `synthesize_answer` (ownership documented in docstring). |
| **Exports** | `analytics/query_engine/__init__.py` — re-exports `synthesize_answer`, `run_analytics_qna`. |
| **Fixtures** | `analytics/query_engine/fixtures.py` — `sample_evidence_bundle_refused`, `sample_evidence_bundle_low_confidence`, `sample_evidence_bundle_low_volume` for tests. |
| **Tests** | `analytics/tests/test_qna_synthesis.py` — mocks `common.llm_adapter.complete` / `analytics.query_engine.synthesis.complete`; refusal, flags, ledger merge, LLM failure, citations/periods; `run_analytics_qna` pipeline test. |
| **Demo CLI** | `scripts/demo_qna_synthesis_metrics.py` — JSON output (timings, `SynthesisResponse`, costs); `--full-pipeline`, `--live` (gated). |
| **Lint** | `pyproject.toml` — `T201` for demo script stdout; `N999` for `analytics/query_engine/__init__.py` (Ruff / repo path false positive). |
| **Streamlit** | `dashboard/streamlit_app.py` — insert repository root on `sys.path` before `from common...` so `streamlit run dashboard/streamlit_app.py` resolves `common`. |

**Not in this commit:** implementation of `build_evidence_bundle` in `evidence.py` (Developer 1). Threshold values in `analytics/query_engine/constants.py` unchanged.

---

## Commands

Run from repository root with Python 3.11 venv active unless noted.

### Unit tests (QnA synthesis)

```powershell
cd "C:\Users\milob\OneDrive\Escritorio\WAI Code\job-intelligence-engine"
py -3.11 -m pytest analytics/tests/test_qna_synthesis.py -v
```

Validates synthesis behavior with a mocked completion path (no live LLM required).

### Unit tests (routing + evidence + guardrails)

```powershell
py -3.11 -m pytest analytics/tests/test_qna_routing.py analytics/tests/test_qna_evidence.py analytics/tests/test_qna_grounding.py analytics/tests/test_sql_guardrails.py -v
```

Covers guardrailed `run_guardrailed_analytics_query`, SQL execution failure messaging, evidence bundle policy, numeric grounding, and `validate_sql`.

### Ruff (touched paths)

```powershell
py -3.11 -m ruff check analytics/query_engine/ analytics/tests/test_qna_synthesis.py scripts/demo_qna_synthesis_metrics.py
```

### QnA synthesis metrics demo (default)

```powershell
py -3.11 scripts/demo_qna_synthesis_metrics.py
```

Uses a fixture `EvidenceBundle`, prints JSON (timings, full `SynthesisResponse`, `total_cost_usd`, `cost_breakdown_usd`). Default run forces `LLM_PROVIDER=mock` and uses a deterministic stub for `_invoke_qna_completion` so output is stable.

### Demo with full evidence path

```powershell
py -3.11 scripts/demo_qna_synthesis_metrics.py --full-pipeline
```

Attempts `build_evidence_bundle(sample_query_result_payload_ok())`. If evidence is still a stub, prints a JSON error and a hint to use fixture mode.

### Demo with live LLM (real API / cost)

```powershell
$env:ANALYTICS_QNA_LIVE = "1"
py -3.11 scripts/demo_qna_synthesis_metrics.py --live
```

Exits with code 2 if `ANALYTICS_QNA_LIVE` is not set to `1`. Uses real synthesis (no completion stub). Ensure provider credentials and spend are intended.

### Streamlit dashboard

```powershell
py -3.11 -m streamlit run dashboard/streamlit_app.py
```

---

## Environment variables

### Always relevant for DB-backed features

| Variable | Purpose |
|----------|---------|
| `PYTHON_DATABASE_URL` | SQLAlchemy PostgreSQL URL (`postgresql+psycopg2://...`). Required for seeding, pipeline, and paths that persist to `llm_audit_log` via `log_extraction_event`. |

### LLM and synthesis (`synthesize_answer` / live demo)

| Variable | Purpose |
|----------|---------|
| `LLM_PROVIDER` | `mock` (forced for non-`--live` demo), `azure_openai`, `anthropic`, or `gemini`. Selects completion backend in `synthesis._invoke_qna_completion`. |
| `ANALYTICS_QNA_LIVE` | Must be exactly `1` for `scripts/demo_qna_synthesis_metrics.py --live`. |
| `ANALYTICS_QNA_SYNTHESIS_MODEL` | Optional; main synthesis model when using Anthropic path (fallback chain includes `EXTRACTION_MODEL_SKILLS`). |
| `ANALYTICS_QNA_FOLLOWUP_MODEL` | Optional; follow-up call model for Anthropic (default `claude-haiku-4-5`). |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Deployment / tier resolution and cost metadata on Azure paths. |
| **Azure OpenAI** | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and related vars per `.env.example` when `LLM_PROVIDER=azure_openai`. |
| **Anthropic** | Dependencies and env per `common/llm_adapter` when `LLM_PROVIDER=anthropic`. |
| **Gemini** | `GEMINI_API_KEY`, `GEMINI_MODEL` (and install) when `LLM_PROVIDER=gemini`. |

### PowerShell vs bash

Prefix style `ANALYTICS_QNA_LIVE=1 command` is **bash**. In **PowerShell** use:

```powershell
$env:ANALYTICS_QNA_LIVE = "1"
py -3.11 scripts/demo_qna_synthesis_metrics.py --live
```

---

## Quick verification checklist

1. `py -3.11 -m pytest analytics/tests/test_qna_synthesis.py -v` — all pass or one skip (evidence stub).  
2. `py -3.11 -m pytest analytics/tests/test_qna_routing.py analytics/tests/test_qna_evidence.py -v` — routing + evidence (includes SQL failure detail assertions).  
3. `py -3.11 scripts/demo_qna_synthesis_metrics.py` — JSON to stdout, no import errors.  
4. `py -3.11 -m streamlit run dashboard/streamlit_app.py` — no `ModuleNotFoundError: common`; Ask the Data shows **Database error (debug)** when execute fails after guardrails.
