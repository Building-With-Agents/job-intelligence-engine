# JIE#366 — Company-name canonicalization: two divergent code paths + staging-table drift

> **Update 2026-05-03 (post-authorship audit):** the original framing was wrong — the
> sophisticated canonicalizer already exists and was authored by Juan Reyes for JIE#95.
> This issue is now scoped to **convergence** + **staging-table application**, not
> "build a normalizer."

## TL;DR

Two divergent company-resolution paths in the codebase, plus zero canonicalization on staging tables, produce visible audit-table drift. The sophisticated path (Juan Reyes / BryanPMX, fuzzy match + legal suffix stripping) exists and works — it just isn't used everywhere it should be, and its suffix list misses geographic suffixes.

## What's already built (and works)

[`enrichment/resolvers/company_resolver.py`](../../enrichment/resolvers/company_resolver.py) — authored by **Juan Reyes** for JIE#95 (2026-03-24), extended by **BryanPMX** (rapidfuzz import, 2026-04-02), small touch-up by **Gary** (2026-04-10):

- `normalize_company_name(raw)` — lowercases, collapses whitespace, strips legal-form suffixes (`corporation`, `incorporated`, `limited`, `llc`, `llp`, `corp`, `inc`, `ltd`, `lp`, `co`).
- `resolve_company(raw_name, session)` — exact normalized match → fuzzy match (rapidfuzz `token_sort_ratio * 0.6 + partial_ratio * 0.4` ≥ `FUZZY_THRESHOLD=85`) → placeholder.
- `lookup_company_exact`, `find_best_fuzzy_match`, `create_placeholder_company`.

This handles most of the bucket-C variants from the JIE#209 audit:

| Variant A | Variant B | Resolved by `normalize_company_name`? |
|---|---|---|
| `'Helen of Troy'` | `'Helen of Troy Limited'` | ✅ — `Limited` is in `_LEGAL_SUFFIXES` |
| `'MAXIMUS'` | `'Maximus'` | ✅ — lowercased |
| `'MANTECH'` | `'ManTech International'` | ❌ — `International` not in suffix list |
| `'X Development LLC'` | `'X, the moonshot factory'` | ❌ — vanity name, beyond rule-based scope |
| `'CITY OF EL PASO, TX'` | `'City of El Paso'` | ❌ — geographic suffix not stripped |
| `'New Mexico State University'` | `'New Mexico state Universoty'` | ⚠ — typo; only fuzzy match would catch (score ~95) |
| `'Helenoftroy'` | `'Helen of Troy'` | ⚠ — whitespace fold; only fuzzy match would catch |

So the existing canonicalizer handles ~50% of audit cases out of the box and another ~30% via the fuzzy fallback. The remaining ~20% (geographic suffixes, vanity names) need explicit rule extension.

## The two divergent paths

| Path | Calls `resolve_company()`? | What it actually does |
|---|---|---|
| **`enrichment/agent.py`** main enrichment flow | ✅ Yes — via `from enrichment.resolvers.company_resolver import resolve_company` ([enrichment/agent.py:95](../../enrichment/agent.py:95)) | Full normalize → exact → fuzzy → placeholder pipeline |
| **`enrichment/job_postings_promotion.py`** `_resolve_or_create_company` ([line 307](../../enrichment/job_postings_promotion.py:307)) | ❌ No — implements its own `_RESOLVE_COMPANY_BY_NAME_SQL` ([line 256](../../enrichment/job_postings_promotion.py:256)) | Only `WHERE LOWER(TRIM(company_name)) = LOWER(TRIM(:company_name))` exact match — no suffix stripping, no fuzzy fallback |
| **`ingestion/sources/jsearch_adapter.py:134`** | ❌ No | `.strip() or "Unknown"` only |
| **`normalization/agent.py:172`** | ❌ No | Pass-through |

The promotion-side resolver was authored by **Gary** (2026-04-02, commit `c8756e35`) two weeks after Juan's resolver landed. It duplicates the same surface area with weaker semantics. As a result, downstream `job_postings.company_id` resolution depends on which code path the row took — and the same employer string can resolve to different `company_id`s depending on whether the enrichment async path or the promotion sync path handled it.

## Evidence: staging-table drift on the SoT DB (2026-05-03)

24 dup groups in `dbo.raw_ingested_jobs` where the same `(source, external_id)` has multiple `company` strings that all refer to the same employer. Eight failure modes:

1. **Casing-only**: `'MAXIMUS' ≡ 'Maximus'`
2. **State suffix**: `'CITY OF EL PASO, TX' ≡ 'City of El Paso'`
3. **Corporate suffix not in `_LEGAL_SUFFIXES`**: `'ManTech International' ≡ 'MANTECH'`
4. **Whitespace fold**: `'Helenoftroy' ≡ 'Helen of Troy'`
5. **Typos**: `'Universoty' ≡ 'University'`
6. **Article prefix**: `'The University of Texas at El Paso' ≡ 'University of Texas at El Paso'`
7. **Vanity vs legal name**: `'X Development LLC' ≡ 'X, the moonshot factory'`
8. **Aggregator vs original**: `'Virtual Vocations Inc' ≡ <original employer>` (separate concern)

Audit query — see [docs/findings/jie209-eid-collisions-2026-05-03.txt](../../docs/findings/jie209-eid-collisions-2026-05-03.txt) for full output:

```sql
WITH dup_groups AS (
  SELECT source, external_id FROM dbo.raw_ingested_jobs WHERE source='jsearch'
  GROUP BY source, external_id HAVING COUNT(*) > 1
)
SELECT external_id,
       COUNT(*) AS n_rows,
       COUNT(DISTINCT LOWER(BTRIM(REGEXP_REPLACE(coalesce(company,''), '[[:punct:]]+$', '')))) AS dc,
       STRING_AGG(DISTINCT company, ' | ') AS companies_seen
  FROM dbo.raw_ingested_jobs r
  JOIN dup_groups d ON d.source=r.source AND d.external_id=r.external_id
 GROUP BY external_id
HAVING COUNT(DISTINCT LOWER(BTRIM(REGEXP_REPLACE(coalesce(company,''), '[[:punct:]]+$', '')))) > 1;
-- 24 rows on SoT DB, 2026-05-03
```

## Why this matters

- **Audit-table drift.** `raw_ingested_jobs` and `normalized_jobs` show the same employer under N strings; staging-table analytics (per-employer posting counts before promotion) over-count distinct employers.
- **Future dedup logic depends on it.** Once JIE#209 lands, the storage hash drops `date_posted` and includes `[source, external_id, title, company]`. With un-canonicalized `company`, `'MAXIMUS'` vs `'Maximus'` produces different hashes → both get admitted. Canonical normalization upstream prevents the regression.
- **Per-row LLM extraction is duplicated.** Skill / tool extraction runs once per `extracted_intelligence` row. Without canonicalization the same logical job runs through extraction multiple times.
- **Two paths diverge over time.** Any improvement to one resolver (e.g., adding a new suffix to Juan's list) doesn't help the other (Gary's exact-SQL path). Long-term maintenance hazard.

## Out of scope for this issue

- **Aggregator listings** (`Virtual Vocations Inc` re-listing canonical Maximus jobs). These are *legitimately distinct* postings of the *same role* by different sources. Track in a sibling issue if needed.
- **Cross-`external_id` fuzzy dedup** (same job posted under different external_ids on different sources). Different problem; Phase 2 fuzzy-dedup work covers it.
- **DRY audit of the rest of the codebase.** Gary deferred this to a separate audit pass; the company-resolver divergence is documented here as a concrete trigger but the broader audit is its own work item.

## Proposed scope

Three convergence + extension tracks, doable in any order:

### 1. Converge the two resolution paths
- Replace `enrichment/job_postings_promotion._resolve_or_create_company` with a call to `enrichment.resolvers.company_resolver.resolve_company`.
- Drop `_RESOLVE_COMPANY_BY_NAME_SQL` and `_INSERT_PLACEHOLDER_COMPANY_SQL` from the promotion module if `resolve_company` covers everything (it does: exact → fuzzy → placeholder).
- Regression: re-run promotion against historical normalized rows and verify same `company_id` per row.

### 2. Extend the suffix list + add geographic-suffix handling
- Add `'international'`, `'group'`, `'holdings'`, `'company'`, `'enterprises'`, `'systems'` to `_LEGAL_SUFFIXES` (audit-driven; refine list with a coverage pass against `dbo.companies.company_name`).
- Add a separate geographic-suffix stripper before `normalize_company_name` (or as an optional flag): trailing `, [A-Z]{2}` (state codes), trailing `, USA`, trailing `, U.S.A.`, etc. Keep these conservative — only strip when the suffix is clearly geographic.
- Regression test: each of the 8 failure modes above produces the same `normalize_company_name` output.

### 3. Apply canonicalization at staging time
- Currently `normalize_company_name` only runs at enrichment / promotion. Staging tables (`raw_ingested_jobs.company`, `normalized_jobs.company`) keep the raw form.
- Decision needed (ADR): apply canonicalization at ingestion (earliest), normalization (semantically correct), or via a separate `company_canonical` column?
- **Recommendation**: add a `company_canonical TEXT` column on `normalized_jobs` populated by `normalize_company_name` at normalization-time. Keep `raw_ingested_jobs.company` raw for audit. Field-mappers in `ingestion/sources/` stay simple.
- Backfill: same playbook as JIE#209 cleanup — export fixtures, populate `company_canonical` for existing rows, re-export.

## Acceptance criteria

- [ ] Audit doc in `eval/findings-company-resolver-divergence.md`: enumerate calls to `resolve_company` vs `_resolve_or_create_company`; sample 50 rows where the same string would resolve differently between the two paths.
- [ ] Track 1: `_resolve_or_create_company` reduced to a thin wrapper around `resolve_company`, or deleted.
- [ ] Track 2: `_LEGAL_SUFFIXES` extended; geographic-suffix stripper added; regression tests covering 8 failure modes.
- [ ] Track 3: ADR on staging-time canonicalization; column added (or alternative chosen); backfill script + export-fixtures-first protocol.
- [ ] Re-run the JIE#209 audit query post-fix; expect ≤ 5 residual variant groups (down from 24). Remaining ≤5 should all be true edge cases (vanity names, etc.) for follow-up.

## Demo-day implication

None. The May 6 demo runs against `development @ 369315c` and is not affected — the locked questions don't aggregate over raw `company` strings on staging tables, and `job_postings.company_id` is already populated (via whichever path each row took) for the questions that touch it.

## Connects to

- [JIE#209](https://github.com/Building-With-Agents/job-intelligence-engine/issues/209) — surfaced this divergence during the dedup audit.
- [JIE#95](https://github.com/Building-With-Agents/job-intelligence-engine/issues/95) — Juan Reyes's original company-resolver work (the one we should consolidate around).
- [JIE#363](https://github.com/Building-With-Agents/job-intelligence-engine/issues/363) — investigation-first pattern.
- [docs/findings/jie209-eid-collisions-2026-05-03.txt](../../docs/findings/jie209-eid-collisions-2026-05-03.txt) — full audit evidence.
- [docs/findings/jie209-stratB-spotcheck-2026-05-03.txt](../../docs/findings/jie209-stratB-spotcheck-2026-05-03.txt) — bucket-C examples.
