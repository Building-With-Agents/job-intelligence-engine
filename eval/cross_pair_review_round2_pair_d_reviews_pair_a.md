# Cross-pair review — Round 2  
**Pair D reviews Pair A corpus:** disruption **gq-001–gq-010** and emergence **gq-011–gq-020** from `eval/qa_golden_questions.json`

**Context:** Pair D did not re-execute each question end-to-end for this note. Judgments combine golden intent labels, `eval/runs/findings-dev-verify-2026-05-01.md` (cohort-level refusal and misroute patterns), and per-item scores from `eval/runs/qa-dev-verify-2026-05-01.json` where cited.

---

## Disruption (gq-001 – gq-010)

**gq-001** — Intent **disruption** is appropriate: composition shift across temporal buckets is exactly the disruption framing. On the 2026-05-01 run the cohort showed depressed evidence citation while intent stayed correct; SQL likely hit disruption aggregates but synthesis often ran into thin or gated evidence. Grounding risk is moderate: answers should cite period-pair deltas, not generic AI narrative.

**gq-002** — Share of postings with AI-assistant tools is a clean disruption metric; routing to disruption is correct. SQL shape should be posting-weighted counts by period; eval run showed the same low evidence-citation pattern as other disruption items. Synthesis must anchor percentages to returned rows or refuse explicitly.

**gq-003** — Declining volume plus automation keywords is a defensible displacement proxy. Intent routing fits. Router/SQL complexity is high (joint trend + keyword signals); expect guardrails or sparse cells. Grounding depends on having enough non-empty period slices in aggregates.

**gq-004** — Data-analyst transformation across early_genai vs agentic_era matches disruption vs pure trend because displacement/new-skill contrast is explicit. SQL should align to analyst slice and era columns. Misclassification risk toward **trend** if prompt over-emphasizes “growth” language only — golden label disruption is right.

**gq-005** — QA/test automation AI-tool share mirrors gq-002 structurally; disruption intent is correct. Evidence path likely skill/posting joins with period filters. Synthesis grounded only if tool mentions are actually present in evidence snippets.

**gq-006** — Long-history roles with steep decline is sophisticated disruption; intent is correct. SQL likely role-level time series; partial data would hurt citation score. Pair D: watch for conflating “decline” with overall market noise.

**gq-007** — “New in last 12 months” rate in data engineering blends emergence and disruption; **disruption** is acceptable if framed as transformation vs additive. SQL must define “new” crisply. Grounding is sensitive to velocity table definitions.

**gq-008** — DevOps/SRE AI-assisted ops share: same family as gq-002/gq-005. Routing disruption OK. Evidence citation in dev-verify was among the slightly higher disruption band (~0.54) but still tight; synthesis should quote posting-derived shares only.

**gq-009** — Legal/regtech displacement: narrow sector filter; disruption intent correct. SQL risk is small denominators; synthesis should not invent sector-level claims without rows.

**gq-010** — Cross-role AI-density threshold narrative is core disruption. Intent correct. Query likely heavy (all IT postings); latency and row caps matter. Grounding requires explicit threshold math from evidence tables, not hand-waved percentages.

---

## Emergence (gq-011 – gq-020)

**gq-011** — First-seen titles post-agentic_era with zero pre_chatgpt matches is textbook **emergence**. SQL should be title-first-seen or equivalent; classifier should resist **geographic** unless location is the subject. Dev-verify scored strong composite (~0.95).

**gq-012** — AI-core titles first appearing post_gpt4: emergence intent correct. SQL likely title cohort with first_posting_date; synthesis must list titles present in evidence. Good demo-adjacent question if evidence rows exist.

**gq-013** — Skills absent early_genai but present now: emergence, not trend (first appearance). Taxonomy coverage drives recall; embedding router quality matters here. Evidence citation was strong on dev-verify.

**gq-014** — Borderplex roles &lt;5 pre-ChatGPT to 50+ in agentic_era: flagship emergence story; also in the provisional 7-question demo set. Intent routing correct; SQL must join role volumes across periods. Grounding is good when velocity/skill tables populate.

**gq-015** — Legal/regtech AI-native titles: emergence with sector filter. Similar SQL caution to gq-009. Intent correct; refuse or narrow if sector rows are empty.

**gq-016** — Employer types driving AI-agent-developer emergence: **emergence** subject is roles by employer type; dev-verify showed **intent_accuracy 0** (misroute — findings triage called out employer vs emergence confusion). SQL likely needs employer_profiles or posting employer join; synthesis was not trustworthy until routing fixed.

**gq-017** — Share mentioning tools that “did not exist” pre_chatgpt: emergence/tool-first-seen framing. Same run showed **intent_accuracy 0** (often pulled toward **disruption** per findings #347 notes). SQL time-bucket + tool mention logic must be exact; synthesis otherwise over-claims.

**gq-018** — AI-native roles with high AI density from first posting: emergence. Routing should avoid pure **disruption** unless displacement language dominates; golden emergence is right. SQL density definition must match pipeline.

**gq-019** — Healthcare-IT + fintech emerging roles by velocity: emergence with sector filters. Composite mid-high on dev-verify; evidence moderate. SQL joins sectors + velocity; check for thin cells.

**gq-020** — New certifications in agentic_era: emergence (credentials as signals). Extraction quality on requirements text matters; SQL may lean on text/JSON paths. Evidence citation was weaker (~0.45) on dev-verify — flag for Pair A if answers drift from postings.

---

## Summary for Pair A

- **gq-001–gq-010:** Intent labels align with Pair A’s disruption charter; the shared pain on the 2026-05-01 run was **evidence citation / refusals** (taxonomy gate, sparse disruption fingerprints), not systematic wrong intent.  
- **gq-011–gq-015, gq-018–gq-020:** Generally aligned; strongest harness scores on first-seen titles/skills (gq-011–gq-014, gq-013).  
- **gq-016, gq-017:** Highest priority for Pair A + orchestration: **misclassification** on dev-verify undermines SQL and synthesis regardless of query shape.
