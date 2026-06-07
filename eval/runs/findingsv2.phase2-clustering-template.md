# Phase 2 Clustering Findings — [DATE] — Tier [N]

**Issue:** [#327](https://github.com/Building-With-Agents/job-intelligence-engine/issues/327)
**Discipline:** ONE variable changed vs prior run.

---

## Run parameters (full snapshot)

| Parameter | Prior value | This run | Changed? |
|-----------|-------------|----------|----------|
| `min_cluster_size` | 3 | X | |
| `min_samples` | 3 | X | |
| `cluster_selection_method` | leaf | X | |
| `umap_n_components` | 15 | X | |
| `umap_n_neighbors` | 10 | X | |
| `umap_min_dist` | 0.0 | X | |
| `umap_metric` | cosine | X | |

**Changed this run:** `param_name` from `old` → `new`

**Command used:**
```bash
CLUSTER_PARAM=VALUE python scripts/run_clustering.py --dry-run --findings-output eval/runs/findingsv2.X-clustering-tierN.md
```

---

## Results

| Metric | Value | Target | Gate |
|--------|-------|--------|------|
| eligible_posting_count | X | — | — |
| cluster_count | X | ≥ 20 | ✗/✓ |
| noise_rate | X% | < 30% | ✗/✓ |
| mega_cluster_share | X% | < 10% | ✗/✓ |

**Phase 2 gate:** PASS / FAIL

---

## Top clusters (qualitative review — sample 5 postings per cluster)

| # | Label | Posts | Share | Top skills |
|---|-------|-------|-------|-----------|
| 1 | | | % | |
| 2 | | | % | |
| 3 | | | % | |

**Semantic coherence check:** Are the top 3 clusters semantically distinct roles, or are similar roles merged?

---

## Decision

- [ ] Gate passed (cluster_count ≥ 20, mega_cluster_share < 10%, noise_rate < 30%)
- [ ] Cluster labels are semantically coherent (checked 5 sample postings per top cluster)
- [ ] No regression on previously identified clusters (SRE, Data Engineering still distinct)

**Decision:**
- [ ] Ship this config to `config/clustering.yaml` + advance to Phase 3
- [ ] Advance to Phase 2 priority 2 (next tuning lever)
- [ ] Revisit this lever (explain why)

**Next step:** <!-- priority 2: min_cluster_size sweep / priority 3: n_components sweep / etc. -->

---

## UMAP convergence diagnostics (fill from stdout)

- Embedding shape after UMAP: `(N_samples, N_components)`
- Any UMAP warnings: none / list warnings
- Run time: approx X minutes

---

## Notes

<!-- Any qualitative observations, surprises, or follow-up questions -->
