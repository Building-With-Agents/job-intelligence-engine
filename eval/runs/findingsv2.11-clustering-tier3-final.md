# Tier 3 final — clustering smoke thresholds (#327 Phase 5)

**Refs:** #327

## Locked in this PR (code-side)

| Check | Threshold | Source |
|-------|-----------|--------|
| `canonical_roles` rows | **≥ 20** | `scripts/smoke/e2e_clustering_cosine.py` |
| Noise proxy (`canonical_role_id` NULL / non-spam slice) | **< 30%** | same |
| Mega-cluster share | **< 10%** | same (`MAX(cluster_size)/assigned`) |

## Live DB

Gary’s fixture / manifest PR (if heavy binaries split from the code PR) should attach the **post-smoke** `noise_fraction`, `role_count`, and mega % from this script’s stdout.
