# v2-scorer-redesign — Run Findings

**Run date:** April 27, 2026  
**Branch:** `feat/eval-scorer-redesign` (PR #278)  
**Langfuse experiment:** `qa-golden-v2-scorer-redesign`  
**Run name:** `v2-scorer-redesign`  
**Questions evaluated:** 90 (full corpus, `qa_golden_questions.json`)  
**LLM routing:** `LLM_DEFAULT=chat-gpt41mini` · `LLM_SYNTHESIS=chat-gpt41`

---

## What changed in v2

The v2 scorer is a full redesign of `eval/qa_scoring.py` driven by epic JIE #272.
Five issues landed on branch `feat/eval-scorer-redesign`:

| Issue | Change | Why |
|-------|--------|-----|
| **#263** | Infrastructure failures return `None` instead of `0.0` for content metrics | Separates pipeline health from answer quality; keeps denominators honest |
| **#261** | `intent_accuracy` is binary (1.0 exact / 0.0 else) | Partial credit hid routing failures; v1's 0.81 meant ~11% were near-misses counted at 0.5 |
| **#269** | `answerability` returns `None` on infra failure (not `0.0`); new optional golden fields (`data_backed`, `expected_min_rows`, `zero_rows_is_correct`, `refusal_appropriate`); new `correct_refusal` score for intent-only items | Distinguishes "pipeline down" from "legitimately empty result" |
| **#260** | Refusal rubric: data-backed refusals penalized at `rubric × 0.35`; intent-only refusals at `rubric × 0.50` | v1 let committed-but-low-evidence answers pass; v2 grades refusals as quality failures |
| **#267 + #268** | `confidence_flags` → `confidence_self_consistency`; new `confidence_in_expected_range`; JIE #268 sub-composites (prompt / classification / pipeline / safety / overall geometric) with gate | Provides a structured breakdown instead of a single black-box composite |

All 90 golden questions were annotated with v2 fields: `expected_confidence_range`, `refusal_appropriate`, `expected_min_rows` (48 data-backed items), and `zero_rows_is_correct` (2 niche geography items).

---

## v1 vs v2 scorecard

| Metric | v1-baseline | v2-scorer-redesign | Δ | Notes |
|--------|------------|-------------------|---|-------|
| `intent_accuracy` | **0.811** | **0.756** | −0.055 | Binary in v2 (no partial credit); 68/90 exact matches |
| `evidence_citation` | **0.691** | **0.337** | −0.354 | Refusal rubric penalizes ~87 refused items; real quality signal |
| `confidence_self_consistency` | 1.000 (as `confidence_flags`) | **1.000** | 0.000 | Model correctly self-flags low confidence; stable |
| `latency_sla` | 1.000 | **1.000** | 0.000 | All items under 45 s SLA; stable |
| **Four-metric composite** | **0.875** | **0.773** | **−0.102** | Δ is scorer + pipeline; see analysis below |
| `answerability` | 0.260 (n=50) | **0.060** (n=50) | −0.200 | Only 3/50 data-backed returned rows |
| `correct_refusal` | N/A | **0.000** (n=40) | — | All 40 intent-only items refused (0/40 committed) |
| `confidence_in_expected_range` | N/A | **0.181** | — | New v2 metric; confidence often below expected floor |
| `overall_geometric_composite` | N/A | **0.510** | — | New v2 sub-composite; gated (see below) |
| `subcomposite_gated` | N/A | **GATED** | — | Answerability 0.06 < 0.20 threshold |

### Sub-composites (v2 new, JIE #268)

| Sub-composite | Score | Interpretation |
|---------------|-------|----------------|
| `prompt_quality_composite` | 0.337 | Proxies evidence_citation (LLM judge #265 pending) |
| `classification_composite` | 0.756 | Intent routing accuracy — the best performing dimension |
| `pipeline_health_composite` | 0.374 | Evidence + answerability blend; gated by data sparsity |
| `safety_composite` | 0.713 | Confidence self-consistency + refusal adherence blend |
| `overall_geometric_composite` | 0.510 | Geometric mean; **GATED** due to answerability < 0.20 |

---

## What the numbers mean

### The composite drop (−0.102) has two causes

**Cause 1 — Scorer tightening (expected, good)**  
The v2 refusal rubric penalizes data-backed refusals at `rubric × 0.35`. In v1 these items received a score based on rubric overlap alone (no refusal penalty). With ~87/90 items refusing in v2, evidence_citation dropped from 0.69 → 0.34. This is the correct signal — the pipeline is refusing when it should be committing.

**Cause 2 — Pipeline regression (requires investigation)**  
In v1 13/50 data-backed items returned rows (answerability 0.26). In v2 only 3/50 did (answerability 0.06). This is not explained by the scorer change alone — the pipeline became more restrictive between runs. Likely candidates:
- Config changes from PR #280 (config YAML migration) may have shifted query filters or threshold defaults.
- The `posted_date` filter or `weeks_back=12` window may exclude the available corpus on a different code path.
- The PR #285 DB re-export may have changed data availability in the local seed.

### intent_accuracy drop (−0.055) is expected and informative

v1 partial credit (0.5 for near-miss intents) masked routing failures. Under the binary v2 scorer:
- **`disruption`** is the worst-performing intent: 2/10 exact matches (0.200). The classifier confuses disruption ↔ comparison and disruption ↔ role_evolution.
- **`comparison`, `curriculum`, `geographic`**: 10/10 perfect routing.
- **`workflow`, `employer`, `emergence`**: 6/10 (60%) — significant miss rate.

### refusal_correctness_rate = 0.000 is a pipeline signal, not a scorer bug

All 40 intent-only items (disruption, emergence, trend, role_evolution) received `correct_refusal = 0.0`, meaning the pipeline refused on every one. These golden questions have `refusal_appropriate: false` — we expect the pipeline to synthesize an analytical answer from available data, not refuse. The refusal pattern (`sufficiency=no_data`) means the router is finding 0 rows for these intents given the current corpus and window.

**This is the most actionable signal from v2:** the pipeline is refusing 100% of analytical intent questions. Whether this is a data issue (sparse corpus), a router issue (too-narrow query), or a config regression from #280/#285 needs investigation before v3.

### confidence_in_expected_range = 0.181

The model's stated confidence is frequently below the expected floor (e.g., `[0.45, 0.90]` for medium questions). Most responses carry confidence 0.0 due to refusals. This metric will be more informative once the pipeline starts returning data.

---

## Intent accuracy breakdown (v2 binary scoring)

| Intent | Exact matches | n | Accuracy | Key failures |
|--------|--------------|---|----------|--------------|
| comparison | 10 | 10 | 1.00 | — |
| curriculum | 10 | 10 | 1.00 | — |
| geographic | 10 | 10 | 1.00 | — |
| role_evolution | 9 | 10 | 0.90 | 1 misrouted to `employer` |
| trend | 9 | 10 | 0.90 | 1 misrouted |
| emergence | 6 | 10 | 0.60 | 4 misrouted |
| employer | 6 | 10 | 0.60 | 4 misrouted (→ comparison, geographic) |
| workflow | 6 | 10 | 0.60 | 4 misrouted (→ employer most common) |
| **disruption** | **2** | **10** | **0.20** | 8 misrouted (→ comparison, role_evolution, trend) |

**Priority fix:** `disruption` intent routing. 8/10 failures with confusion spread across 3 other intents suggests the classifier prompt or few-shot examples need disruption-specific strengthening. File as a v3 target.

---

## What's next

### Immediate — Manual scoring (Layer 2)

The automated scorer covers syntax, routing, and pipeline health but not **answer quality**. The v1 manual scoring rubric established Layer 2. For v2 we need:

- Select the **worst 15 items by composite** (the bottom of the evidence_citation distribution since most items are in the 0.19–0.55 band).
- Score each on: `evidence_grounding`, `actionability`, `data_accuracy`, `refusal_appropriateness`.
- Focus manual effort on the **3 items that returned data** (answerability = 1.0) — these are the only items where the full quality rubric is assessable.
- Also score **2–3 disruption misroutes** to understand whether the answer was passable despite wrong intent.

### Short term — Pipeline investigation (pre-v3)

1. **Root cause the answerability collapse** (0.26 → 0.06). Compare query plans for a data-backed item (`gq-049` is a good test case — `zero_rows_is_correct: true`, so it should score 1.0 regardless). Check if the router's `weeks_back` window, geo filter, or table mapping changed after #280.
2. **Root cause 100% refusal on intent-only items.** Check `skill_velocity` and `skill_demand_weekly` table row counts for the current DB seed against the v1 baseline. If empty, the data pipeline didn't re-seed after #285.
3. **Disruption intent fine-tuning.** The classifier confuses disruption with comparison and role_evolution. Add 2–3 disruption-specific few-shot examples to the intent classification prompt.

### Medium term — v3 run targets

Once pipeline issues are fixed:
- Target `intent_accuracy ≥ 0.85` (fix disruption; currently at 0.756)
- Target `answerability ≥ 0.60` (pipeline returns data; currently 0.06)
- Target `refusal_correctness_rate ≥ 0.70` (pipeline commits on analytical intents)
- Target `evidence_citation ≥ 0.55` (pipeline commits + evidence grounding improves)
- Implement LLM judge for evidence_citation (`feat/eval-observability`, issue #265) to replace heuristic rubric

### After v3 — Issue #265 (LLM judge)

`prompt_quality_composite` currently proxies `evidence_citation`. The heuristic rubric (must-include keyword overlap) is imprecise — it gives `workflow` items a 0.195 score even when the answer is a clean refusal with no evidence. An LLM judge on 3–5 axes would fix this. Planned in `feat/eval-observability`.

---

## Issue #287 — qa_prompt_iteration_log.md recommendation

Gary's question: drop (A), strip to unique content (B), or keep + automate (C)?

**Recommendation: Option B — strip to unique content, with one addition.**

After working through the v2 run end-to-end, my honest assessment:

**What I actually used the log for during v1:**
- The per-version deep-dive sections helped me spot the `evidence_citation` distribution shape before looking at Langfuse (faster to scan markdown than the UI for "what's the story"). But Gary is right that this is transcription overhead — this findings document proves you can write the narrative faster post-run than maintaining per-run sections.
- The DEV-NNN registry is genuinely unique and irreplaceable. DEV-001 through DEV-004 are not captured anywhere else.

**What should stay (Option B stripped format):**
- Title block + intro sentence
- Version-history table (5-column: version / date / change / hypothesis / Langfuse URL) — this is the seam; Langfuse holds the numbers, the table holds the arc
- DEV-NNN deviation registry — keep in-file or extract to `eval/HARNESS_DEVIATIONS.md`; either works

**What should move out:**
- Per-version deep-dive sections → replaced by this pattern: a `findingsv<N>.md` file per major run milestone (like this document), checked into `eval/runs/`. Gary can read it, link to it from the PR comment, and it doesn't need ongoing maintenance.
- Per-item scores, worst-N → Langfuse
- Layer 2 manual scores → Langfuse score comments

**The addition:** adopt `eval/runs/findings<vN>.md` as the standard artifact for major run milestones (v1-baseline, v2-scorer-redesign, v3-post-pipeline-fix, etc.). One document per milestone, written post-run, no ongoing maintenance.

**DEV-NNN:** Keep inline in the stripped log for now (it's 4 entries). Extract to `eval/HARNESS_DEVIATIONS.md` only when it grows past ~10 entries.

My answer to Gary's specific questions:
1. The per-intent breakdown helped once (discovered disruption vs. comparison confusion). But the intent confusion matrix in the console output + Langfuse now captures this automatically.
2. Prompt-change rationale has been going in the markdown only. Moving it to Langfuse run descriptions makes sense — I'll do that for v3.
3. v1 was a one-time anchor; subsequent runs should be lighter (findings doc only, no deep-dive log maintenance).
4. Preference: **B + findings-per-milestone pattern**.
5. Yes, DEV-NNN stays under all options.

---

## Appendix — Raw run data

- **v1-baseline JSON:** `eval/runs/qa-v1-baseline.json`
- **v2-scorer-redesign JSON:** `eval/runs/qa-v2-scorer-redesign.json`
- **v2 Langfuse experiment:** `qa-golden-v2-scorer-redesign` on `langfuse.watechcoalition.org`
- **v1-baseline-rescored JSON:** `eval/runs/qa-v1-baseline-rescored.json` *(run `scripts/rescore_v1_baseline.py` against cloud Langfuse to generate)*
