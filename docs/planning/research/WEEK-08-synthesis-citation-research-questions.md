# WEEK-08 — Synthesis Engine + Evidence Citation + Confidence Scoring

| | |
|---|---|
| **Owner(s)** | Bryan + Emilio |
| **Timing** | Mid Week 8 (Exercise 8.3 — Pair C) |
| **Stakes** | High |
| **Output** | `synthesis.py` module in `analytics/query_engine/` + one-page findings doc |
| **Timebox** | 3-4 hours |

---

## The question

How do you take **raw query results** and synthesize a **natural language answer** that:

- cites **specific evidence**,
- is **transparent** about confidence and data volume,
- **refuses to hallucinate** when data is insufficient, and
- **scaffolds** the user toward deeper exploration with follow-up questions?

Intent classification and routing (Pair B) produce structured query results — rows and counts from aggregate tables. A table of numbers is not an answer. The synthesis engine transforms those results into a response a workforce planner can act on. The hard part is not fluent text — it is ensuring **every claim references real data**, the system **admits uncertainty**, and it **never fabricates** a statistic to fill a gap.

---

## What you have (scaffolding on `development`)

- **Intent classification + routing output** — Classified intent, extracted entities, structured query results (rows from aggregate tables).
- **Provider-agnostic LLM adapter** — Synthesis should use **Sonnet-class** for quality. LLM pricing resolves correctly (PR #142 fixed overcount) — use `common/llm_adapter.py` **PRICING** dict for cost tracking; do not hardcode prices.
- **Haiku-class LLM adapter** — For follow-up question generation (cheaper, speed-focused).
- **`analytics/query_engine/`** — Directory structure from Pair B (`intent.py`, `router.py`).
- **Response quality requirements** — Evidence citation, confidence transparency, volume transparency, temporal precision, refusal on hallucination.
- **Upstream data** — `job_postings`, `extracted_intelligence` populated for dev; aggregate tables are often filled by running the analytics pipeline. Synthesis receives **pre-queried data from the router** — you do not need fully populated analytics fixtures to start.

---

## What you must build

- Synthesis engine: NL answers from structured query results.
- Evidence citation: every claim references concrete data (table name, row count, time period).
- Confidence scoring + **transparency flags** when confidence &lt; 0.6.
- **Volume transparency** when the answer is based on **fewer than 30 postings**.
- **Hallucination refusal** when data is genuinely insufficient.
- **Follow-up scaffolding** — 2–3 contextually relevant follow-up questions after each answer.
- **Per-query cost tracking** — classification + routing + synthesis + follow-up (tokens → cost via adapter pricing).

---

## Task checklist

- [ ] Create `analytics/query_engine/synthesis.py` with synthesis engine.
- [ ] NL answer generation using Sonnet-class LLM.
- [ ] Evidence citation: every claim tied to table, count, time period.
- [ ] Confidence scoring from data volume + query match quality.
- [ ] Flag + explain when confidence &lt; 0.6 (explanation must be actionable, not only “low confidence”).
- [ ] Flag when based on &lt; 30 postings.
- [ ] Temporal precision: every answer states which period(s) the data covers.
- [ ] Refusal pattern when data is insufficient (distinguish **zero rows** vs **sparse rows**).
- [ ] Follow-ups via Haiku-class LLM; validate they relate to answer topic, not generic filler.
- [ ] Per-query cost sum across all LLM stages.
- [ ] Tests: low volume, low-confidence classification, missing data.
- [ ] Verify citations reference specific counts and periods.
- [ ] One-page findings: What I Tested, What I Found, Recommendation, Tradeoffs, Data/Evidence.

---

## Evaluation criteria

| Criterion | Target |
|---|---|
| Evidence citation | Every factual claim references specific data (table, count, period). |
| Confidence transparency | Flag behaves correctly when confidence &lt; 0.6. |
| Volume transparency | Flag when based on &lt; 30 postings. |
| Hallucination refusal | Refuses when data is genuinely insufficient. |
| Temporal precision | Time period(s) always explicit. |
| Follow-up quality | Contextually relevant, not generic. |
| Cost tracking | Accurate sum of all LLM calls in the pipeline. |

**Decision rule:** Citations and transparency flags are **quality requirements**, not polish. Fluent but uncited = failure. Clear uncertainty = success.

---

## Questions to ask yourself

- What counts as **evidence** in a citation — is “based on 47 job postings from Q1 2025” enough, or must you cite **table + query slice**?
- How do you stop the LLM from **plausible statistics** not present in the result set? Which **grounding** and **constraint** patterns work best for **tabular** evidence (not chunk RAG)?
- At confidence **0.55**, what **actionable** explanation do you show (not just “low confidence”)?
- What does a **good refusal** look like vs a lazy “I don’t know” — e.g. sparse data with caveats vs zero rows?
- Should follow-ups be driven by **answer content**, **original intent**, or **both**?

---

## Cursor prompts (narrow research)

- Explain techniques for **grounding** LLM text in **structured** data — how production systems ensure claims match retrieved/tabular evidence and reduce hallucination.
- **Best practices for confidence scoring** in text-to-SQL when the score must reflect **query relevance** and **data sufficiency**.
- How systems generate **contextually relevant follow-up questions** for deeper exploration (not generic “tell me more”).

---

## Findings doc template (one page)

### What I tested

### What I found

### Recommendation

### Tradeoffs acknowledged

### Data / evidence

---

## Reference files (repo)

- `docs/planning/ARCHITECTURE_DEEP.md` — Analytics / Q&A synthesis, evidence, response quality.
- `docs/planning/ARCHITECTURAL_DECISIONS.md` — Decision #40 (Workforce Q&A), if present.
- `analytics/query_engine/` — Pair B `intent.py`, `router.py` (when merged).
- `common/llm_adapter.py` — Provider-agnostic calls + **PRICING** for cost tracking.

### External pre-reading (optional)

- [Why Citation-Based RAG Still Hallucinates](https://www.whyaitech.com/notes/systems-note-002.html) — unfaithful citations, hallucinated bridges.
- [Measuring LLM Groundedness in RAG Systems](https://www.deepset.ai/blog/rag-llm-evaluation-groundedness) — faithfulness / groundedness framing.
- [Index-RAG: Citation-first approach](https://medium.com/@praneeth.v/index-rag-citation-first-approach-to-rag-0e948b9e12c1) — cite-then-synthesize pipeline.

---

## Gemini Deep Research — master prompt (copy everything below the line)

Use **Gemini Deep Research** (or equivalent deep-research mode). Paste the block **verbatim** as the user message. Refine dates or product names if your lesson materials differ.

---

**ROLE:** You are a research assistant producing an evidence-backed technical brief for senior engineers building a **workforce analytics Q&A synthesis layer**.

**CONTEXT — SYSTEM WE ARE BUILDING**

We operate a **labor-market job intelligence pipeline** (PostgreSQL, aggregate analytics tables, prior steps: intent classification + **structured query execution**). The router returns **tabular results** (rows, counts, dimensions, time buckets) — not retrieved document chunks. We must implement:

1. **`synthesis.py`** — turns structured results into **natural language** for workforce planners.
2. **Evidence citation** — claims must trace to **specific** evidence: which logical table/dataset, **N rows** or postings supporting the claim, **time period** covered.
3. **Confidence scoring** — combine signals such as **match quality** (intent ↔ SQL ↔ results), **volume** (e.g. warn if &lt; 30 postings), **coverage** across time, and **data gaps**. **Flag and explain** when confidence &lt; **0.6**; explanations must be **actionable** (why low: sparse slice, ambiguous intent, partial time range, etc.).
4. **Volume transparency** — always surface sample size; warn when below policy threshold (e.g. **&lt; 30 postings**).
5. **Temporal precision** — every answer states **which period(s)** the statistics cover.
6. **Hallucination / over-claiming refusal** — if data is insufficient, **do not invent** numbers; distinguish **no data** (zero rows) from **sparse data** (some rows but not representative).
7. **Follow-up scaffolding** — **2–3** follow-up questions after each answer; must be **contextually relevant** (topic + geography + time + comparisons), not generic.
8. **Cost tracking** — per user query, sum **token usage → USD** across: classification, routing/SQL, **synthesis (Sonnet-class)**, follow-ups (**Haiku-class**); pricing comes from a central **PRICING** dict (no hardcoded $/1K).

**GROUNDING MODEL:** Evidence is **SQL result sets and metadata**, not unstructured RAG chunks. We care about patterns that transfer from **structured grounding**, **tool-using LLMs**, and **numeric claim verification** — not only classic document RAG.

**RESEARCH DELIVERABLE — OUTPUT FORMAT**

Produce a **structured report** with the following sections. Use **bullet lists** where possible. For every major recommendation, cite **external sources** (papers, industry blogs, product docs, benchmarks) with **title + URL + one-line relevance**. If the web is contradictory, say so and compare approaches.

1. **Executive summary** (10–15 bullets) — what to implement first vs later.

2. **Grounding & anti-hallucination for tabular / SQL results**
   - Compare: **post-hoc citation** vs **citation-first / index-style** synthesis for **numeric** claims.
   - Known failure modes: **unfaithful citations**, **hallucinated bridges**, **fluent but wrong magnitudes**.
   - Prompting, **JSON/schema constraints**, **chain-of-thought visibility** (internal vs user-facing), **self-verification** steps, **claim-by-claim checking** against the result JSON.
   - What is **proven in production** vs **experimental** (2024–2026)?

3. **Evidence schema design**
   - Minimum fields for a **citation object** (table/slice identifier, row count, time window, SQL hash or fingerprint, optional top contributing rows).
   - How to require the model to **emit claims only with citation IDs** attached.
   - How evaluators test **faithfulness** of each sentence to the cited rows.

4. **Confidence scoring**
   - Decompose into **subscores**: volume, time coverage, intent–SQL alignment, result cardinality, noise (NULLs), etc.
   - How to **calibrate** thresholds (why 0.6 is or is not reasonable).
   - How to generate **user-facing explanations** when score is borderline (e.g. 0.55–0.59).

5. **Refusal & sparse-data UX**
   - Patterns for: **zero rows**, **&lt; threshold postings**, **partial period coverage**.
   - Template language that is **honest** without killing usefulness (e.g. limited view + caveats + what query would help).

6. **Follow-up question generation**
   - Techniques: **intent-anchored** vs **answer-anchored** vs **hybrid**; how to avoid generic questions.
   - Using a **smaller/cheaper model** for follow-ups while keeping **quality gates** (filter irrelevant follow-ups).

7. **Cost & observability**
   - Per-query **cost aggregation** across multi-step LLM pipelines.
   - What to log (tokens, model id, stage name, correlation id) for **audit** without PII.

8. **Evaluation & regression testing**
   - Metrics: **groundedness**, **citation precision/recall**, **numeric exact match** to source cells, **refusal appropriateness**.
   - Suggested **golden tests** (synthetic small tables + edge cases).

9. **Risks & open questions**
   - What we should **prototype** before locking schema (max 5).

10. **Annotated bibliography**
    - Table: **Topic | Source | URL | Why it matters**

**CONSTRAINTS**

- Prefer **recent** sources where the field is fast-moving (2024–2026), but include **foundational** work where still standard.
- Do **not** assume we have a vector RAG index over job descriptions for this exercise — primary evidence is **aggregate/query outputs**.
- Be skeptical of vendor hype; **separate** marketing claims from **evaluated** methods.

**QUALITY BAR**

If you cannot find strong sources for a subtopic, say **“insufficient peer evidence”** and suggest **empirical** tests instead of inventing certainty.

---

*End of Gemini Deep Research master prompt.*
