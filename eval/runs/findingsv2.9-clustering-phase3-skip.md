# Phase 3 — BranchDetector / two-pass (**skipped** this iteration)

**Refs:** #327

## Rationale

`hdbscan.BranchDetector` and two-pass mega splits add **complexity + version coupling** without a measured shortfall on the Phase 1+2 pipeline in this branch. The roadmap allows **explicit skip** pending a live DB profile that still shows mega-cluster **≥10%** after Phase 2 tuning.

## Re-open criteria

- Mega-cluster share still **≥10%** after Phase 2 knob lock **and** UMAP params stable, **or**
- Product requests finer-grained labels inside a single dominant SOC bucket.
