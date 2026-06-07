# Phase 2 — locked config: min_samples=3 + min_cluster_size=3 (#327)

**Refs:** #327, PR #337, PR #402, PR #400 (min_cluster_size), PR #331 (Phase 1 UMAP)

## Locked combined config (development)

| Parameter | Value | Source |
|-----------|-------|--------|
| `clustering.min_samples` | **3** | #327 Phase 2 (#337) |
| `clustering.min_cluster_size` | **3** | #400 |
| `clustering.selection_method` | **leaf** | Phase 1 (#331) |
| `clustering.distance_metric` | **cosine** | #297 |
| `clustering.dim_reduction.method` | **umap** | Phase 1 |
| `clustering.dim_reduction.n_components` | **15** | Phase 1 |
| `clustering.dim_reduction.n_neighbors` | **10** | Phase 1 |
| `clustering.dim_reduction.min_dist` | **0.0** | Phase 1 |
| `clustering.cluster_input_min_quality_score` | **0.0** (disabled) | Phase 4 scaffold |

## Prior measurements (reference only)

### After min_cluster_size 3 alone (#400)

From [`findings-400-hdbscan-min-cluster-size-3.md`](findings-400-hdbscan-min-cluster-size-3.md):

| Metric | Value |
|--------|-------|
| Noise rate (loader-eligible) | **31.4%** |
| Clusters found | **333** |
| Postings assigned | 2,645 / 3,856 (68.6%) |

### Phase 1 UMAP baseline

From [`findingsv2.5-phase1-dim-reduction.md`](findingsv2.5-phase1-dim-reduction.md):

| Metric | Value |
|--------|-------|
| Clusters | **200** |
| Noise (clustering-input denominator) | **36.5%** |
| Mega-cluster share | **1.1%** |

## Combined min_samples=3 + min_cluster_size=3 — live re-run (operator)

**Status:** Pending @theGaryLarson post-merge validation on admin DB.

Run:

```bash
python scripts/run_clustering.py --findings-output eval/runs/findingsv2.8-clustering-phase2-min-samples-3.md
python scripts/smoke/e2e_clustering_cosine.py
```

Paste output below after live run:

| Metric | Target gate | Measured |
|--------|-------------|----------|
| `eligible_posting_count` | — | _TBD_ |
| Clusters found | ≥ 20 | _TBD_ |
| Noise rate | < 30% | _TBD_ |
| Mega-cluster share | < 10% | _TBD_ |

### Qualitative samples (top clusters)

_TBD — paste from `--findings-output` Markdown after live run._

## Phase 2 gate verdict

_TBD — PASS/FAIL after live re-run._
