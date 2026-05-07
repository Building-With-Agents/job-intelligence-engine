# Pair C cohort — Cycle 1 baseline inventory (#340)

**Run date:** 2026-05-04  
**Branch:** `fix/week10-eval-baseline-340-344-345` (pre-SHA: commit after #340 lands)  
**Cohort:** `gq-041` … `gq-060` via `--cohort pair-c-geo-comp`  
**Scorer:** v2 (`eval/qa_scoring.py`)

---

## Scope (harness-only cycle)

This cycle **does not** assert a numeric baseline from a fresh Langfuse or `--dry-run` run in CI. It **locks the evaluation contract**:

- Cohort expansion and **lexicographic** execution order (`eval/qa_eval_cohorts.py`).
- JSON export: per-item **`composite`** aligned with `composite_score`, plus **`composite_mean`**, **`composite_p25`**, **`composite_p25_method`**.
- Langfuse: per-item **`Evaluation(name="composite")`** and run-level **`mean_composite`** when traces exist.

## Next cycles (stacked)

1. Re-run `python -m eval.qa_eval --prompt-version pairc-week10-c1 --cohort pair-c-geo-comp --dry-run --output-json eval/runs/qa-pairc-week10-c1.json` against a populated SoT DB; paste **mean / p25** into `qa_prompt_iteration_log.md` Pair C table.
2. Sort by composite (worst first); classify top-5 failures into intent vs SQL vs synthesis; pick **one** code or prompt change; repeat for cycles 2–3.

## References

- [`eval/runs/findingsv2.md`](findingsv2.md) — findings template.
- [`eval/runs/qa-pairc-week10-harness-contract.json`](qa-pairc-week10-harness-contract.json) — JSON shape contract.
