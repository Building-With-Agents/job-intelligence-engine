# Pair C — Cycle 2 findings: taxonomy supplement for comparison questions (#340)

**Run date:** 2026-05-23
**Branch:** `feat/340-geo-comp-prompt-iterations`
**Cohort:** `gq-041` … `gq-060` via `--cohort pair-c-geo-comp`
**Scorer:** v2 (`eval/qa_scoring.py`)

---

## Diagnosis — top-5 failure skew from `dev-verify-2026-05-01` baseline

Starting from the `dev-verify-2026-05-01` aggregate (comparison composite ~0.849; 4/10 refusing on "data shape"), I inspected `eval/runs/findings-dev-verify-2026-05-01.md` and traced each comparison refusal to its router log key.

| gq-id | Extracted skill_names | Gate result | Root cause |
|-------|----------------------|-------------|------------|
| gq-054 | ["Large Language Models", "LLMs"] | `skill_taxonomy_gate_blocked` | Neither term in `dbo.skills` exact match |
| gq-056 | ["SQL", "ETL"] | `skill_taxonomy_gate_blocked` | "ETL" not in `dbo.skills` ("Extract, Transform, Load" is; "ETL" is not) |
| gq-059 | ["Artificial Intelligence", "Machine Learning"] | passes gate | Both in taxonomy; failure was evidence_citation shape |
| gq-060 | ["Continuous Integration", "CI/CD"] | passes gate | Both in taxonomy; "ci/cd" exact-matches |

**Primary failure bucket: SQL / routing** — taxonomy gate blocks gq-054 and gq-056 before any query fires.

---

## Change made — cycle 2

**File:** `analytics/query_engine/router.py`

**Change summary:** Added `_COMPARISON_SKILL_SUPPLEMENT` frozenset (ETL, LLMs, Generative AI, MLOps, NLP, CI/CD abbreviations, continuous-delivery variants) that passes the `_skill_terms_all_in_dbo_skills` gate without a DB round-trip. A `_TAXONOMY_SUPPLEMENT` alias unifies the set for future extension (JIE #349 will union its AI-tool supplement into the same constant).

The RT-007 exact-match guard is preserved: terms NOT in the supplement still require exact DB lookup. "ETL" passes; "ETL engineer" (a role, not a skill) would still be blocked.

```python
# analytics/query_engine/router.py (additions)
_COMPARISON_SKILL_SUPPLEMENT: frozenset[str] = frozenset({
    "llm", "llms", "large language models", "large language model",
    "generative ai", "genai",
    "etl", "extract transform load", "extract, transform, load",
    "ml", "nlp", "mlops", "devsecops",
    "cd", "ci cd", "continuous deployment", "continuous delivery",
})
```

**Tests added:** 6 unit tests in `analytics/tests/test_router.py::TestComparisonSkillSupplement` — all passing.

---

## Before scores (from `dev-verify-2026-05-01`)

| Metric | Geographic (gq-041–050) | Comparison (gq-051–060) |
|--------|------------------------|------------------------|
| intent_accuracy | 1.000 | 1.000 |
| evidence_citation | 0.749 | 0.394 |
| composite | ~0.937 | ~0.849 |
| answerable (n/10) | 8/10 | 6/10 |

---

## After scores

**Pending Gary SoT re-run.** Expected improvement: gq-054 and gq-056 move from `skill_taxonomy_gate_blocked` to answerable. If `skill_demand_weekly` has weekly rows for "LLM" / "ETL" post-seed (`scripts/seed_ai_taxonomy_terms.py`), evidence_citation should improve. If aggregate tables are sparse for these terms, gq-054/056 will produce a low-confidence answer rather than refusing — a strict improvement.

---

## Next cycle (cycle 3)

Remaining comparison failures (gq-059 thin-data hedge, gq-055 temporal period with no aggregate handler) suggest the synthesis LLM refuses when data is thin rather than stating partial results. Cycle 3 addresses this in `synthesis.py`.

## References

- `eval/runs/findings-dev-verify-2026-05-01.md` — diagnostic source
- `analytics/query_engine/router.py` — change site
- `analytics/tests/test_router.py::TestComparisonSkillSupplement` — regression suite
