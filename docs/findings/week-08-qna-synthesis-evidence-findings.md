# Week 8 Findings — QnA synthesis, evidence citations & truth layer (#117)

| | |
|---|---|
| **Tracking** | [GitHub #117](https://github.com/Building-With-Agents/job-intelligence-engine/issues/117) |
| **Related rule** | `.cursor/rules/analytics-qna-synthesis.mdc` |
| **Intent classification (separate note)** | `docs/findings/week-08-intent-classification-kb-routing.md` — conversational front door (`intent.py`); pairs with router/SQL work below. |

---

## Scope

End-to-end **Ask the Data** path after guardrailed SQL: **`QueryResultPayload` → deterministic evidence → grounded natural-language synthesis** with citations, transparency flags, optional refusal, per-query cost rollup, and post-generation numeric checks. Upstream intent classification and NL→SQL routing are documented alongside in the intent findings doc; this note focuses on **evidence** and **synthesis**.

**Core modules:** `analytics/query_engine/evidence.py`, `analytics/query_engine/synthesis.py`, `analytics/query_engine/grounding.py`, `analytics/query_engine/qna.py`, `analytics/query_engine/schemas.py`, `analytics/query_engine/routing.py`, `analytics/query_engine/ledger_utils.py`.

---

## What we tested

- Deterministic evidence construction from `QueryResultPayload` into `EvidenceBundle` (`build_evidence_bundle`).
- Refusal handling for `router_error`, zero-row results, and structural gaps (e.g. salary-oriented intent without salary metrics in rows).
- **`DataSufficiency`**: `NO_DATA` vs `SPARSE` vs `ADEQUATE`; volume and blended-confidence policy; **`distinct_posting_count`** when the router supplies it.
- Temporal coverage derivation, partial-period notes, and truncated result sets (`result_truncated`).
- **`sql_execution_error_detail`** echo for operator/debug UI when PostgreSQL fails after guardrails.
- **`synthesize_answer`**: refusal path (no main LLM when `refuse_synthesis`); main answer (`role="synthesis"`) + follow-ups (`role="classification"`); `CostLedger` merge; safe fallback when the main LLM call fails.
- **Numeric grounding**: `verify_answer_grounding` — retry with listed unsupported tokens, then template fallback if still failing.
- End-to-end **`run_analytics_qna()`** (`build_evidence_bundle` → `synthesize_answer`).

---

## What we found

- A complete **truth layer** (`EvidenceBundle`) is required before synthesis: `run_analytics_qna()` always runs `build_evidence_bundle()` first; without it the public QnA path could not ship.
- The **Pydantic contract** (`schemas.py`) was stable enough for a deterministic builder; evidence types include `EvidenceCitation`, refusal fields, and optional SQL error detail for API/Streamlit.
- **Zero rows** and **low but non-zero volume** must stay distinct user states (`NO_DATA` refusal vs `SPARSE` with volume warning); collapsing them produces conflicting UI warnings.
- **Salary-oriented intents** need an explicit metric guard: posting counts alone are not sufficient to claim salary figures.
- **Synthesis is not “prompt-only.”** The model receives **citeable facts JSON** (+ `period_coverage`), but **`grounding.py`** deterministically checks numeric tokens in the draft against the bundle corpus, then **one synthesis retry**, then a **fixed grounding fallback** message if numbers still cannot be supported — so fabricated statistics are not silently served.
- **Thresholds** (see `analytics/query_engine/constants.py`): blended confidence transparency **0.6**, volume warning below **30** postings — product-facing copy should stay aligned.

---

## Recommendations

1. Keep **`build_evidence_bundle()`** as the **only** policy gate before any synthesis LLM call.
2. Keep **`verify_answer_grounding`** + retry + fallback in the loop for any change to `synthesize_answer` prompts or citation shape.
3. Have the router return **`distinct_posting_count`** and explicit period signals whenever SQL allows — transparency and volume policy improve.
4. Preserve **`NO_DATA` vs `SPARSE`** through REST and Streamlit consumers.
5. Wire **`needs_clarification`** (intent layer) and refusal/grounding messages consistently in UX (see intent findings doc).

---

## Tradeoffs acknowledged

| Area | Notes |
|---|---|
| **Volume heuristic** | Grouped/multi-row results: use **`QueryResultPayload.distinct_posting_count`** when available; else **max** per-row posting count — conservative vs summing overlapping buckets. |
| **Period coverage** | Derived heuristically from returned columns; no single canonical period field on `QueryResultPayload` yet. |
| **Salary refusal rules** | Intentionally conservative; broader intent-specific validation can follow routing label stabilization. |
| **Numeric grounding** | Token/proximity checks catch invented **numbers**; non-numeric hallucinations need separate monitoring or stricter prompts. |
| **Cost / tiers** | Main answer uses synthesis tier; follow-ups use classification tier — documented in `synthesis.py` and adapter routing. |

---

## Data / evidence (artifacts & commands)

**Implementation**

| Path | Role |
|---|---|
| `analytics/query_engine/evidence.py` | `build_evidence_bundle` — citations, sufficiency, refusal, period/volume heuristics. |
| `analytics/query_engine/synthesis.py` | `synthesize_answer` — LLM calls, transparency flags, grounding retry, safe fallbacks, period prefix. |
| `analytics/query_engine/grounding.py` | `verify_answer_grounding`, `prefix_period_coverage` — deterministic checks, no LLM. |
| `analytics/query_engine/qna.py` | `run_analytics_qna` — orchestration. |
| `analytics/query_engine/routing.py` | Guardrailed query path → payload → QnA. |

**Tests**

| Path | Role |
|---|---|
| `analytics/tests/test_qna_evidence.py` | Zero rows, sparse, classifier confidence, truncation, router errors, salary guard, period ordering, sample citations. |
| `analytics/tests/test_qna_synthesis.py` | Refusal, flags, ledger, LLM failure, `run_analytics_qna` pipeline. |
| `analytics/tests/test_qna_grounding.py` | Verifier + synthesis fallback on hallucinated numbers. |
| `analytics/tests/test_qna_routing.py` | Routing + integration with QnA. |
| `analytics/tests/test_sql_guardrails.py` | `validate_sql` / allowlist. |

**Suggested verification (repo root, venv active)**

```bash
python -m pytest analytics/tests/test_qna_synthesis.py analytics/tests/test_qna_evidence.py analytics/tests/test_qna_grounding.py analytics/tests/test_query_engine_schemas.py -q
```

**Additional context:** `docs/COMMIT_LOG-week08-qna-synthesis.md` (commit-level changelog, demo script, env notes).
