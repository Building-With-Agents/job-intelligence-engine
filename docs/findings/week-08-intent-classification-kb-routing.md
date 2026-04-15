# Week 8 — Intent Classification + Knowledge Base Routing (Findings)

| | |
|---|---|
| **Owner(s)** | Fatima + Nestor |
| **Exercise** | 8.2 (Early–Mid Week 8) |
| **Scope** | Fatima: `analytics/query_engine/intent.py` + intent tests/findings. Router/SQL guardrails: paired teammate scope. |

---

### What I Tested

**Intent classification (`intent.py`).** End-to-end behavior of `classify_workforce_question()`: all ten required intents (`trend`, `role_evolution`, `disruption`, `emergence`, `curriculum`, `employer`, `workflow`, `geographic`, `comparison`, `other`), structured return shape (`intent`, `confidence`, `needs_clarification`, `extracted_entities` with geographic, role, skill, and time lists), default Haiku-class model via `role="classification"`, and error paths (empty input, invalid JSON, fenced markdown JSON, failed or exceptioning `complete()`, unknown intent string from the model, scalar vs list entity payloads).

**Edge cases from the brief.** Ambiguous and multi-topic wording (simulated LLM returning a single primary intent with mid confidence), out-of-domain questions mapped to `other`, confidence threshold boundary behavior (`0.55`), and coercion of entity fields to normalized string lists.

**Live mini-benchmark hook.** Added `@pytest.mark.live_llm` benchmark test (20 labeled prompts) to report intent pass rate and average per-call latency against the real configured Haiku-tier endpoint when run with `pytest --live`.

**Router ownership boundary.** Router implementation and SQL guardrail enforcement are tracked separately with teammate-owned code; no edits were made in Fatima’s intent-only scope.

---

### What I Found

**Intent layer is in good shape for a conversational front door.** Classification uses the shared LLM adapter, logs fallbacks without PII, normalizes intents (including alias handling), strips Markdown JSON code fences from model output, and never raises—failures become `other` with `confidence=0.0`, which keeps downstream code from crashing.

**Single primary intent is an explicit product choice.** The system prompt instructs the model to pick one best intent and lower confidence when ambiguous; that answers “which wins” for hybrid questions (e.g. geographic + comparison) at the cost of not exposing secondary intents unless you extend the schema later.

**Low-confidence handling is now explicit.** Output includes `needs_clarification` (true when confidence < 0.55), so the UX layer can prompt for clarification instead of hard-routing uncertain queries.

**Entity extraction today is LLM-extracted strings, not resolved geography.** “El Paso” appears as extracted text; informal aliases (“EP”, “Sun City”) depend on model generalization or a future gazetteer—there is no dedicated NER or resolver in `intent.py`.

---

### Recommendation

1. **Keep `needs_clarification` wired in API responses** and use it for confidence-aware UX messaging before query execution.
2. **Run the live benchmark regularly** (`pytest analytics/tests/test_intent_classification.py -m live_llm --live`) and log pass rate + latency in the PR notes.
3. **Continue pairing with router owner** so intent confidence and routing behavior remain aligned at integration time.
4. **Add alias dictionaries over time** (geo nicknames, common role abbreviations) to improve entity extraction consistency.

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
| `analytics/tests/test_intent_classification.py` | Automated evidence: category coverage, edge cases, confidence threshold behavior, adapter contract (`agent_name`, `role="classification"`), optional live benchmark (`-m live_llm --live`). |
| `conftest.py` | Live-test gating: `--live` opt-in for `live_llm` benchmark execution. |

**Latest local test run (2026-04-15):**
- Command: `python -m pytest analytics/tests/test_intent_classification.py -v`
- Result: **32 passed, 1 skipped** (`test_live_intent_classification_mini_benchmark` skipped by design unless run with `--live`).

**Note:** This page now reflects intent-classification ownership only; router details should be documented by the router owner once integration lands.
