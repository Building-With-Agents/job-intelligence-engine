# Phase 4 — quality gate + post-gate smoke delta (#327)

**Refs:** #327

## Code

- `config/clustering.yaml` → `cluster_input_min_quality_score` (default **0.0** = disabled).
- `analytics/canonical_roles/loader.py` adds optional `AND jp.quality_score >= :min` predicate.

## Mandatory check after raising the gate above 0.0

1. Re-cluster on a DB with **no NULL** `quality_score` for eligible rows (#328).
2. Run `python scripts/smoke/e2e_clustering_cosine.py`.
3. Compare noise %, cluster count, and mega % vs **Phase 2 baseline** logged in `findingsv2.8-*`.

**Baseline (pre–Phase-4 gate, this branch):** record metrics from the first successful live run after merging Phase 2 YAML.

**Post–Phase-4:** paste smoke output + SQL counts here. If deltas exceed team tolerance, run **at most one** additional Phase-2 single-knob experiment (per plan).
