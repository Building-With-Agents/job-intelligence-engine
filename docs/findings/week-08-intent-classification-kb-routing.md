# Week 8 — Intent Classification + Knowledge Base Routing (Findings)

| | |
|---|---|
| **Owner(s)** | Fatima + Nestor |
| **Exercise** | 8.2 (Early–Mid Week 8) |
| **Scope** | `analytics/query_engine/intent.py`, `analytics/query_engine/router.py`, SQL guardrails on generated queries |

---

### What I Tested

**Intent classification (`intent.py`).** End-to-end behavior of `classify_workforce_question()`: all ten required intents (`trend`, `role_evolution`, `disruption`, `emergence`, `curriculum`, `employer`, `workflow`, `geographic`, `comparison`, `other`), structured return shape (`intent`, `confidence`, `extracted_entities` with geographic, role, skill, and time lists), default Haiku-class model (`INTENT_CLASSIFICATION_MODEL` override), and error paths (empty input, invalid JSON, fenced markdown JSON, failed or exceptioning `complete()`, unknown intent string from the model, scalar vs list entity payloads).

**Edge cases from the brief.** Ambiguous and multi-topic wording (simulated LLM returning a single primary intent with mid confidence), out-of-domain questions mapped to `other`, and coercion of entity fields to normalized string lists.

**Knowledge base routing (`router.py`).** The exercise requires intent→aggregate-table mapping, parameterized SQL, and guardrail validation before execution. **Current repo state:** `router.py` is a placeholder only; there is **no** automated test yet for generated SQL, table coverage per intent, or guardrail enforcement on router output.

**Live accuracy (15–20 diverse questions).** Not run as a labeled benchmark in CI; evaluation criteria in the brief should be satisfied with a small hand-checked or spreadsheet-backed set once the router executes real queries.

---

### What I Found

**Intent layer is in good shape for a conversational front door.** Classification uses the shared LLM adapter, logs fallbacks without PII, normalizes intents (including a small alias map), strips Markdown JSON code fences from model output, and never raises—failures become `other` with `confidence=0.0`, which keeps downstream code from crashing.

**Single primary intent is an explicit product choice.** The system prompt instructs the model to pick one best intent and lower confidence when ambiguous; that answers “which wins” for hybrid questions (e.g. geographic + comparison) at the cost of not exposing secondary intents unless you extend the schema later.

**Routing and SQL are the remaining gap relative to the exercise checklist.** Without `router.py` implementing the intent→table matrix (e.g. `trend` → `skill_demand_weekly` / `tool_demand_weekly` / `skill_velocity`, `geographic` → `geo_demand_weekly`, etc.), parameterized queries, and a single choke point that runs existing **SELECT-only / allowlist / LIMIT / timeout** guardrails, the pipeline stops at classification. Aggregate tables exist upstream of this module, but this layer does not yet bind questions to them.

**Entity extraction today is LLM-extracted strings, not resolved geography.** “El Paso” appears as extracted text; informal aliases (“EP”, “Sun City”) depend on model generalization or a future gazetteer—there is no dedicated NER or resolver in `intent.py`.

---

### Recommendation

1. **Implement `router.py`** as specified: a declarative map from each of the ten intents to allowed tables and query templates; build SQL with bound parameters only; pass every final string through the project’s SQL guardrail helper before `session.execute` (see `.cursor/rules/sql-guardrails.mdc` and `docs/planning/ARCHITECTURE_DEEP.md` for the Workforce Q&A path).
2. **Clarify vs route:** define a confidence floor (for example 0.5–0.55). Below it, return a clarification prompt instead of hitting aggregates, while still logging intent for tuning.
3. **Close the evaluation loop:** maintain 15–20 labeled questions (spread across intents + edge cases) and re-score after prompt or router changes; track latency for one Haiku classification call in the full question-to-answer path.
4. **Zero-row answers:** when SQL is valid but empty, return an explicit “no matching postings in scope” narrative rather than treating it as an error—especially for niche skills (“quantum computing”) that still classify as `trend`.

---

### Tradeoffs Acknowledged

| Tradeoff | Notes |
|---|---|
| **One intent vs multi-intent** | Simplifies routing and SQL templates; may under-represent compound questions unless confidence + clarification UX compensate. |
| **Haiku-class vs stronger models** | Better cost and latency for per-turn classification; edge taxonomy or entity nuance may need occasional Sonnet fallback or periodic prompt tuning—not yet measured end-to-end. |
| **Safe `other` fallback** | Protects availability but can hide systematic mislabels until you monitor intent distribution and user feedback. |
| **LLM entities vs NER** | Fewer moving parts than a separate NER service; resolution quality for nicknames and disambiguation is weaker without a reference geography/role dictionary. |

---

### Data / Evidence

| Artifact | Role |
|---|---|
| `analytics/query_engine/intent.py` | Implementation: categories, prompt, `classify_workforce_question`, Pydantic validation. |
| `analytics/tests/test_intent_classification.py` | Automated evidence: category coverage, edge cases, adapter contract (`agent_name`, Haiku-class model in call kwargs). Run: `pytest analytics/tests/test_intent_classification.py -v`. |
| `analytics/query_engine/router.py` | **Stub** — to be replaced with KB routing + guarded SQL per exercise. |

**Note:** The prior standalone `docs/EXP-005_*` write-up was removed in favor of this Week 8 one-pager aligned to Exercise 8.2.
