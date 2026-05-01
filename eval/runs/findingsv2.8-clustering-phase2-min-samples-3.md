# Phase 2 — `min_samples` 5 → 3 (#327)

**Refs:** #327

| Knob | Old | New |
|------|-----|-----|
| `clustering.min_samples` | 5 | **3** |

**Hypothesis:** On UMAP-reduced embeddings, a slightly more permissive density floor increases clustered mass without returning to raw 1536-D over-merging.

**Measurements to paste after Gary’s live re-run:** `eligible_posting_count`, noise %, cluster count, mega-cluster %, qualitative label samples.
