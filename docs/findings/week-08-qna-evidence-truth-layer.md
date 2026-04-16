# Week 8 Findings — QnA evidence truth layer (#117)

**Last updated:** 2026-04-16  
**Scope:** Deterministic **`build_evidence_bundle`** (`analytics/query_engine/evidence.py`), handoff from **`QueryResultPayload`**, and how evidence drives refusals, transparency, and synthesis inputs. Complements the voice layer (`synthesis.py`, `grounding.py`) and routing (`routing.py`).

---

## Current status (vs early Week 8)

The truth layer is **shipped** and wired on **both** entrypoints: guardrailed NL→SQL (**`run_guardrailed_analytics_query`**) and the **REST** ORM path that still ends in **`qna.run_analytics_qna`**. The earlier blocker (“evidence missing so `run_analytics_qna` could not run”) is **resolved**. Remaining work is mostly **router payload richness** (canonical periods, distinct posting counts) and **text-to-SQL column hygiene** (schema hints)—not gaps in `build_evidence_bundle` itself.

---

## What we tested

- Deterministic evidence construction from **`QueryResultPayload`** into **`EvidenceBundle`** (citations, sufficiency, blended confidence, refusal flags).
- Refusal handling for **`router_error`**, SQL execution failures (with optional **`sql_execution_error_detail`**), and **zero-row** payloads (**`NO_DATA`**).
- **Volume** and **confidence** policy for **sparse** vs adequate samples (**`SPARSE`** vs **`ADEQUATE`**), including distinct-posting vs max-per-row heuristics.
- **Temporal coverage**: row-level period extraction (including **`week_start`** and **`velocity_week`**), **multi-period** strings ordered **oldest → newest** for grounded summaries, partial-period and truncation cases.
- **REST / API parity**: transparency fields and citation metadata (**`supporting_count`**, **`time_period`**, etc.) on evidence items where applicable.
- **Audit**: Ask-the-Data **SQL validation** attempts logged to **`llm_audit_log`** (paired with routing hardening).
- **Regression tests**: `analytics/tests/test_qna_evidence.py`, `analytics/tests/test_qna_synthesis.py` (including **`test_refusal_skips_llm_and_sets_message`** — synthesis **`complete`** not called on refuse), routing tests as integrated.

---

## What we found

1. **`build_evidence_bundle()` must stay a hard gate before synthesis.** It is the right place for **policy** (refuse, volume, salary guard, structural gaps) without invoking the main LLM.
2. **`NO_DATA` and `SPARSE` must stay distinct.** **No rows** → refuse synthesis and **skip** the main voice model; **small N** → allow synthesis but set **volume transparency** and cautious copy. Collapsing them confuses users (empty vs “thin but real”).
3. **Salary-oriented intents** need **metric columns** in the result (e.g. median / p25 / p75), not posting counts alone—otherwise we **refuse** or structural-guard correctly.
4. **Period quality improved** when routers return **`week_start`** / **`velocity_week`** (and existing **`time_period`** keys)—evidence **recognizes** them; multi-period coverage is now **sorted** for readable “data period” language.
5. **Bad NL-generated SQL** (e.g. nonexistent **`posting_date`** / **`role`**) fails **after** validation at **Postgres**—evidence then builds a **refusal** bundle with **no facts**. That is **not** a truth-layer bug; it is correct “no evidence to ground.” Mitigation is **routing schema hints** and allowlist (**including `canonical_roles`** for role **labels**), not loosening evidence rules.
6. **Synthesis anti-fabrication** is layered: prompts use **citeable facts JSON**, **`refuse_synthesis`** short-circuits, and **`verify_answer_grounding`** can **retry** then apply a **safe fallback** if numeric claims still drift—evidence defines what “supported” means.

---

## Recommendations

- Keep **`build_evidence_bundle()`** as the **only** deterministic policy gate before the main synthesis call.
- Ask routing / SQL owners to keep populating **`distinct_posting_count`** when SQL can express **`COUNT(DISTINCT job_posting_id)`**, and explicit **period** fields whenever possible—evidence transparency degrades gracefully but **determinism improves** with better columns.
- Preserve **`NO_DATA`** vs **`SPARSE`** semantics and surface them consistently in **Streamlit** and **REST** (flags, refusal vs answer body, follow-ups empty on refuse).
- Continue extending **`test_qna_evidence.py`** when new intents or columns appear; keep **`test_refusal_skips_llm_and_sets_message`** green to prove **no Sonnet spend** on hard refusal.

---

## Tradeoffs acknowledged

- **Volume:** Best-effort when grouped rows return; prefer **`QueryResultPayload.distinct_posting_count`** when available; else **max** per-row posting-like counts (conservative vs summing overlapping buckets).
- **Periods:** Still partly **heuristic** from returned row keys until **`QueryResultPayload`** carries a single canonical period field; **`period unknown`** remains a valid, honest outcome.
- **Salary refusals:** Intentionally **conservative** structural rules; broaden only with product sign-off and schema support.
- **Dual SQL validators** (aggregate sqlglot vs Ask-the-Data regex allowlist) are an **integration** concern; evidence only sees **`QueryResultPayload`**—either path must populate rows/columns consistently for evidence quality.

---

## Commands / evidence for reviewers

```bash
cd /Users/bryanpmx/Documents/Projects/job-intelligence-engine
source .venv/bin/activate

# Evidence + schema + synthesis shape
python -m pytest analytics/tests/test_qna_evidence.py analytics/tests/test_qna_synthesis.py analytics/tests/test_query_engine_schemas.py -q

# Refusal skips main LLM (demo-friendly proof)
python -m pytest analytics/tests/test_qna_synthesis.py::test_refusal_skips_llm_and_sets_message -v

# Optional: routing → evidence → synthesis (mocked LLM + session)
python -m pytest analytics/tests/test_qna_routing.py -q --tb=short
```

Lint (paths may grow with edits):

```bash
python -m ruff check analytics/query_engine/evidence.py analytics/query_engine/fixtures.py analytics/query_engine/qna.py analytics/tests/test_qna_evidence.py analytics/tests/test_qna_synthesis.py
```

---

## Related docs

- Demo / lead walkthrough: **`docs/Week 8/DEMO-script-pair-c-ask-the-data.md`**
- Cursor rule: **`.cursor/rules/analytics-qna-synthesis.mdc`**
- Contract: **`analytics/query_engine/schemas.py`**
