# Week 8 — Q&A synthesis & evidence (delivery checklist)

**Epic:** [Synthesis engine + evidence citation + confidence scoring (#117)](https://github.com/Building-With-Agents/job-intelligence-engine/issues/117)

**Runbook:** [`WEEK-08-synthesis-evidence-citation-bryan-emilio-runbook.md`](./WEEK-08-synthesis-evidence-citation-bryan-emilio-runbook.md)  
**Reading:** [`IMP-027-synthesis-evidence-citation-bryan-emilio-reading.md`](./IMP-027-synthesis-evidence-citation-bryan-emilio-reading.md)  
**Research pack (optional):** [`docs/planning/research/WEEK-08-synthesis-citation-research-questions.md`](../../docs/planning/research/WEEK-08-synthesis-citation-research-questions.md) — design questions, external links, narrow Cursor prompts, and a copy-paste **Gemini Deep Research** prompt. Use before implementation if you want a literature-style brief; it does **not** override this checklist or `schemas.py`.

**Cursor rule:** [`.cursor/rules/analytics-qna-synthesis.mdc`](../../.cursor/rules/analytics-qna-synthesis.mdc) — engineering conventions for this workstream; it does **not** replace the Pydantic contract in code.

---

## Frozen team agreement (follow this)

This is what the team committed to so work can proceed without blocking on a live design session:

1. **Executable contract** lives in **`analytics/query_engine/schemas.py`**:  
   - **`QueryResultPayload`** — filled by the **intent + SQL routing** workstream after guardrailed execution (`QueryRequest`, intent label, classification confidence, columns, rows, counts, truncation flag, tables touched, optional errors, correlation id).  
   - **`EvidenceBundle`** / **`EvidenceCitation`** — built **before** the main synthesis LLM call; all citeable facts the model may use.  
   - **`SynthesisResponse`** — API-style result: answer, citations, confidence flags, volume flags, refusal, follow-ups, **`total_cost_usd`** and optional breakdown.  
   - **`CostLedger`** / **`LLMCallCost`** — accumulate per-leg spend across the full Q&A path. **USD amounts** must come from **`common/llm_adapter`** (return metadata and central pricing — e.g. **`PRICING`**); do **not** hardcode per-1K token rates inside `query_engine`.

2. **Thresholds** live in **`analytics/query_engine/constants.py`** (`CONFIDENCE_TRANSPARENCY_THRESHOLD`, `VOLUME_WARNING_POSTING_THRESHOLD`). Change only with product sign-off and update the Cursor rule + runbook in the same change.

3. **Ownership boundary:** Routing/classification/SQL generation is **out of scope** for this checklist’s implementers unless explicitly taken on; those engineers populate **`QueryResultPayload`**. This checklist’s owners implement **`build_evidence_bundle`** and **`synthesize_answer`** (and related tests).

4. **Stubs:** If routing is not on `main` yet, ship **fixtures/stubs** that still validate against **`QueryResultPayload`** so evidence and synthesis can be developed and tested in CI.

5. **Project rules:** Obey all applicable `.cursor/rules/` (especially **`sql-guardrails.mdc`**, **`python-agent-patterns.mdc`**) for production-quality, consistent code.

If this agreement changes, update **`schemas.py`** first, then tests, the **analytics-qna-synthesis** rule, and the runbook (and **ARCHITECTURE_DEEP.md** if the external contract changes).

---

## Division of work — two developers

Same delivery; split by layer to reduce coupling and review risk. Coordinate on **`schemas.py`** when adding fields.

### Developer 1 — Truth layer (evidence & policy)

**Delivers**

- **`build_evidence_bundle`** in `analytics/query_engine/evidence.py`: derive **`EvidenceCitation`** rows, **`DataSufficiency`**, blended confidence, **`confidence_explanation`**, **`refuse_synthesis`** / **`refusal_reason`** from **`QueryResultPayload`**.
- Deterministic rules: volume and confidence thresholds, zero rows vs sparse sample, temporal coverage string.
- **Unit tests** (no network, no live LLM): edge cases from the table below.

**Does not own**

- Main synthesis prompts, Sonnet orchestration, or follow-up LLM calls (unless pairing on prompt constraints).

### Developer 2 — Voice layer (synthesis & orchestration)

**Delivers**

- **`synthesize_answer`** in `analytics/query_engine/synthesis.py`: Sonnet-class generation **strictly** from **`EvidenceBundle`**; Haiku-class follow-ups; populate **`SynthesisResponse`**.
- **Cost rollup:** merge **`CostLedger`** entries from classification, routing (when provided), synthesis, and follow-ups using **`common/llm_adapter`** return metadata and shared pricing (same path the research doc calls out — avoid duplicate or magic dollar constants).
- **Integration tests** with **mock LLM**: schema, refusals, grounded numbers matching bundle.
- Public entrypoint wiring: call evidence builder then synthesis; document for analytics API / Streamlit when integrated.

**Coordinates with Developer 1 on**

- Any change to **`EvidenceBundle`** or **`SynthesisResponse`** fields.

---

## Edge cases (both should cover in tests)

| Scenario | Expected behavior |
|----------|-------------------|
| Zero rows | Clear “no data in scope” (not the same as “low volume”). |
| Small N (&lt; 30 postings) | Volume warning; no overconfident market claims. |
| Classification confidence &lt; 0.6 | Transparency flag + explanation. |
| Large truncated result set | Summarize only from supplied rows/aggregates; cite truncation when relevant. |
| Sparse or inconsistent columns | Refuse or caveat; do not interpolate missing periods. |
| LLM failure | Retries per project norms; no fabricated statistics; no PII in logs. |
| Grounding | No statistics in prose that are not supported by **`EvidenceBundle`**. |

---

## Integration with other codepaths

| Partner | Alignment |
|---------|-----------|
| **Intent + SQL routing** | Implemented as **`analytics/query_engine/routing.py`** (`run_guardrailed_analytics_query`) + **`sql_guardrails.py`**; Streamlit **Ask the Data** calls it with a read-only session. |
| **Analytics agent / HTTP Q&A** | Response shape should expose citations, flags, follow-ups, and total cost when wired. |
| **Streamlit “Ask the Data”** | Surface warnings, sample size, and periods. |
| **Observability** | Preserve model/tokens/cost tracing via existing adapter and audit patterns. |

---

## Deliverables (#117 + runbook)

- [x] `analytics/query_engine/synthesis.py` — production synthesis path (not only stubs).
- [x] `analytics/query_engine/evidence.py` — production **`build_evidence_bundle`**.
- [x] Evidence citation: claims trace to **table / count / period** where applicable (`EvidenceCitation` + UI / Streamlit).
- [x] Confidence, volume, temporal, and refusal behavior per runbook.
- [x] 2–3 contextual follow-up questions (cheaper tier).
- [x] Per-query **total** LLM cost across legs.
- [x] Tests: unit (policy), integration (mock LLM), runbook spot cases.
- [x] Short findings note (tested / found / recommendation / tradeoffs / evidence) per team convention.
- [x] Post-generation grounding hardening: reject or fall back when synthesized numeric claims are not supported by **`EvidenceBundle`** (`grounding.py` + synthesis retry + tests).

---

## Production-ready bar

- [x] No PII in logs; structured reason codes where helpful.
- [x] CI runs tests without live LLM and without mutating shared audit tables (see **`testing-standards.mdc`**).
- [x] Env and model-tier usage documented for operators (`.env.example` — `ANALYTICS_QNA_LIVE`, `LLM_*` for Q&A).
- [x] Handoff note for routing owners: **`QueryResultPayload`** field expectations after guardrailed SQL.
- [x] PR references **#117** and the **runbook** and/or **`.cursor/rules/analytics-qna-synthesis.mdc`** (see `docs/COMMIT_LOG-week08-qna-synthesis.md` + this file).

### Routing handoff note

For strong evidence quality, routing should reliably populate these **`QueryResultPayload`** fields after guardrailed SQL:

- **Always required:** `request.query`, `intent_label`, `classification_confidence`, `columns`, `rows`, `row_count_returned`, `result_truncated`, `tables_referenced`.
- **Strongly recommended for tracing/failures:** `router_error` when execution/validation fails, plus `correlation_id` when available.
- **Strongly recommended inside returned rows:** an explicit posting-count field such as `posting_count`, plus explicit period fields such as `time_period`, `period`, `period_start`, or `period_end`.
- **Intent-specific requirement:** if the intent is salary-oriented, the rows must include the requested salary metric columns (for example `median_salary`, `p25_salary`, `p75_salary`) rather than only counts.
- **Quality note:** evidence quality degrades to caveats or refusal when posting-count or period fields are missing, because the truth layer cannot verify sample size or temporal coverage deterministically.

---

## Suggested sequence

1. Confirm **`schemas.py`** + **`fixtures.py`** meet both developers’ needs; adjust once if gaps appear.  
2. In parallel: Developer 1 implements evidence + policy tests; Developer 2 implements synthesis + cost + mock integration tests.  
3. Integrate routing output when available; expand tests; ship findings.
