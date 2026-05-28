# Pair C — Cycle 3 findings: synthesis comparison clause (#340)

**Run date:** 2026-05-23
**Branch:** `feat/340-geo-comp-prompt-iterations`
**Cohort:** `gq-041` … `gq-060` via `--cohort pair-c-geo-comp`
**Scorer:** v2 (`eval/qa_scoring.py`)
**Stacked on:** Cycle 2 (taxonomy supplement)

---

## Diagnosis — residual failures after cycle 2

After cycle 2 removes the taxonomy gate block for gq-054 and gq-056, the remaining comparison failures are:

| gq-id | Question (short) | Failure pattern |
|-------|-----------------|----------------|
| gq-055 | "Compare Borderplex IT posting volume across the four temporal periods" | Routes to `sector_summary_weekly` (no skill_names extracted); returns 0–2 rows if aggregates are sparse; synthesis refuses with "insufficient data" |
| gq-058 | "Credential-inflation rate (cert requirement share) healthcare vs fintech" | Complex multi-table derivation; synthesis sees thin facts and hedges rather than stating what is known |
| gq-059 | "Artificial Intelligence vs Machine Learning" | Gate passes; but `skill_demand_weekly` may return sparse counts; synthesis hedges instead of stating partial leader |

**Primary failure bucket: synthesis** — the synthesis LLM over-hedges when data is thin for a comparison question. The existing `_build_main_prompt` instructions say "if facts are thin, keep the answer short and explicitly cautious," which the LLM interprets as permission to refuse rather than give a partial answer.

---

## Change made — cycle 3

**File:** `analytics/query_engine/synthesis.py`

**Change summary:** Added `comparison_clause` to `_build_main_prompt`. When `intent_label == "comparison"`, the clause instructs the LLM to:
1. State the **magnitude difference** (e.g. "X has 3× more postings than Y") when both sides have data
2. **Identify the leader** explicitly
3. When only one side has data, name which side is absent and why
4. Do **not** refuse when facts are thin — use caveats instead

This is a synthesis prompt only change: no routing or ORM logic is touched. The grounding check in `grounding.py` still fires after synthesis to catch invented statistics.

```python
# analytics/query_engine/synthesis.py (addition to _build_main_prompt)
if intent_label == "comparison":
    comparison_clause = (
        "- This question is a comparison: when citeable_facts_json includes counts for "
        "two or more skills, sectors, or temporal periods, state the magnitude difference "
        "(e.g. 'X has 3× more postings than Y') and identify the leader. "
        "If only one side has data, be explicit about which side is absent and why. "
        "Do not refuse or hedge when the data is thin — use it with appropriate caveats.\n"
    )
```

---

## Before scores (stacked: post-cycle-2 expected, pre-cycle-3)

| Metric | Comparison (gq-051–060) |
|--------|------------------------|
| evidence_citation | ~0.394 (pre-cycle-2 ref) |
| composite | ~0.849 |
| answerable | 6/10 (expected 8/10 after cycle 2) |

---

## After scores

**Pending Gary SoT re-run.** Expected improvement:
- gq-055 temporal period comparison: if `sector_summary_weekly` has ≥1 row per period, synthesis now states the period ranking rather than hedging
- gq-058 credential-inflation: synthesis produces a partial answer with caveats instead of refusing
- gq-059 AI vs ML: synthesis states the observed counts and identifies the leader even when one period is sparse
- Net: evidence_citation expected to rise from ~0.394 toward ~0.5–0.6; composite from ~0.849 toward ~0.87+

---

## Regression check

- Geographic questions (gq-041–050) unaffected: `comparison_clause` only activates for `intent_label == "comparison"`.
- Existing `test_qna_synthesis.py` tests still pass (no prompt text assertions that would break on the new clause).

## References

- `analytics/query_engine/synthesis.py` — change site (`_build_main_prompt`)
- `eval/runs/findings-pairc-cycle2-taxonomy-supplement.md` — upstream cycle
