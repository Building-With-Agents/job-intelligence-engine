# Quality scorer audit — JIE #329

**Refs:** #329

## Component review (20-posting style matrix)

Representative behaviours (manually traced against `enrichment/classifiers/quality.py`):

| Archetype | Completeness | Clarity | AI keyword | Structural | Notes |
|-----------|-------------|---------|-------------|------------|-------|
| Thin title / short body | Low | Low | Neutral floor | Low | Stays `<0.45` in regression test |
| Strong ops / no AI lexicon | High | High | Neutral **0.58** floor | High | Previously dragged down by 25% AI weight + 0.38 floor |
| ML / GenAI-heavy | High | High | High (density bonus) | High | Reaches **≥0.95** with long body + bullets |

## Decisions (open questions from #329)

1. **Keep** an AI-density signal, but **downweight** it (10%) and **raise** the zero-hit floor **0.38 → 0.58** so non-AI industries are not treated as low-quality by default.
2. **Weights** moved from equal 25% to **30% / 30% / 10% / 30%** (completeness / clarity / AI / structural).
3. **No** post-hoc rank calibration in Phase 1 — keeps scores comparable across snapshots; revisit only with an ADR.
4. **“Perfect” ceiling** — top-tier postings now reach **~0.95–1.0** on synthetic maxed fixtures in `enrichment/tests/test_quality.py`.

## Phase 4 (#327) threshold guidance

After live re-score, expect a wider spread; start `clustering.cluster_input_min_quality_score` tuning around **0.45–0.55** (see `findings-329-post-change-distribution.md`). **Ship YAML at `0.0` (disabled)** until Gary’s clustering re-run validates counts.
