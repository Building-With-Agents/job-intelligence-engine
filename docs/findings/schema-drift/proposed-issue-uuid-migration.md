# architecture: migrate INT serial ids to UUID v4 across agent pipeline tables

> **Status:** discussion / scoping. Not in scope for the May 6 demo. Surfaced 2026-05-03 by Gary as a fix for the recurring sequence-sync class of bug.

## Motivation

A recurring class of seed-script bug ([JIE#181](https://github.com/Building-With-Agents/job-intelligence-engine/issues/181), [JIE#182](https://github.com/Building-With-Agents/job-intelligence-engine/issues/182), [JIE#183](https://github.com/Building-With-Agents/job-intelligence-engine/issues/183), and [the new sequence-sync regression filed alongside this](#)) is rooted in PostgreSQL serial-sequence drift between the local SoT DB and Azure / re-seeded environments. INT serial ids only work cleanly when the sequence stays in lockstep with `MAX(id)` — a fragile invariant that's easy to violate (drop+reimport, fixture-driven inserts with explicit ids, schema migrations that ADD COLUMN to a table mid-stream, cross-environment fixture round-trips).

UUID v4 ids eliminate the entire class of bug because:

- No sequence to keep in sync.
- Every insert generates its own id (`gen_random_uuid()` or app-side `uuid4()`).
- Fixtures are portable across environments forever — no id collisions.
- Re-seeds, drop+reimports, and partial-failure recoveries don't require "remember to call setval afterward."

## Trade-offs

| Aspect | INT serial (current) | UUID v4 |
|---|---|---|
| Sequence sync after seed | **Required** (and easy to forget — recurring bug class) | **Not needed** |
| Cross-env portability | Fixtures break with id collisions; require careful merge logic | Fixtures portable forever |
| Storage per id | 4–8 bytes | 16 bytes (4× larger; meaningful on `llm_audit_log` at 47k+ rows = ~512 KB extra index space per million) |
| Index size | Smaller B-tree, sequential inserts pack densely | Larger B-tree; v4 random ordering causes more page splits and index bloat |
| Query performance | Range scans on id are cheap | Range scans on id are meaningless (no temporal ordering) |
| Debuggability | `id=5234` easy to type | `f814983d-9058-4455-a8d3-e52a3822d933` requires copy/paste |
| Cross-table join cost | Comparable | Comparable |
| Schema migration complexity | n/a (status quo) | High — see below |

## Affected tables

INT-serial id tables that would change:

- `dbo.raw_ingested_jobs.id`
- `dbo.normalized_jobs.id`
- `dbo.extracted_intelligence.id` (also has FK `extracted_intelligence.normalized_job_id` → `normalized_jobs.id`)
- `dbo.normalization_quarantine.id`
- `dbo.llm_audit_log.id`
- `dbo.employer_profiles.id`
- `dbo.cohort_gap_cache.id`
- `dbo.orchestration_audit_log.id`

Tables already using UUID (no change):
- `dbo.job_postings.job_posting_id`
- `dbo.companies.company_id`
- `dbo.canonical_roles.role_id`
- `dbo.job_ingestion_runs.run_id`

## Migration plan (sketch — not yet decided)

### Phase A — schema layered migration (no behavior change yet)

1. ADD COLUMN `uuid_id UUID NOT NULL DEFAULT gen_random_uuid()` to each affected table.
2. Backfill: `gen_random_uuid()` covers existing rows automatically via DEFAULT.
3. CREATE UNIQUE INDEX on `uuid_id`.
4. For `extracted_intelligence`: ADD COLUMN `normalized_job_uuid UUID`; backfill via `UPDATE extracted_intelligence SET normalized_job_uuid = (SELECT uuid_id FROM normalized_jobs WHERE id = extracted_intelligence.normalized_job_id)`.

### Phase B — model + caller migration (behavior change, gated)

5. Update SQLAlchemy models: `Mapped[int]` → `Mapped[uuid.UUID]` for affected `id` columns. Keep INT `id` as a transitional column for one release.
6. Update `extracted_intelligence` FK to point at `normalized_jobs.uuid_id`.
7. Update all callers:
   - `ingestion/agent.py`, `normalization/agent.py`, `skills_extraction/agent.py`, `enrichment/agent.py` — every `int(record["id"])` → `str(record["uuid_id"])`.
   - All scripts under `scripts/` that read agent tables.
   - Analytics queries in `analytics/query_engine/` that touch these tables.
8. Update fixtures: re-export with UUID columns populated.

### Phase C — drop legacy INT id

9. Migration: DROP COLUMN id (after one release of bake time).
10. RENAME uuid_id → id.
11. Re-run regression tests.

### Estimated cost

Conservative: ~3 days of focused engineering for the migration script + model + caller updates + fixture re-export + regression tests. Plus ~1 week of bake time on a feature branch before merging. Plus ADR.

Pessimistic: 5 days if FK rewires surface unexpected coupling.

## Open questions

1. **App-side vs DB-side UUID generation?** Postgres `gen_random_uuid()` (DB-side, no insert latency) vs Python `uuid.uuid4()` (app-side, allows logging the id before insert).
2. **UUID v4 vs UUID v7?** v7 is monotonic on time, which preserves index locality and improves range scans on insert order. Worth using v7 if we're migrating anyway.
3. **Do we keep INT id around during the transition** (dual-column for one release) or hard cutover? Dual-column is safer but doubles index storage temporarily.
4. **Testing surface area.** Every test that uses `assert row.id == 5` needs to change. Likely 50+ test functions affected.

## Demo-day implication

None. This is post-demo work. The May 6 demo runs against `development @ 369315c` with INT ids; the sequence-sync class of bug is invisible there because the demo Postgres stays running.

## Recommended next steps

1. **Pre-decision**: write an ADR under `docs/decisions/ADR-NNN-uuid-id-migration.md` capturing the trade-offs above and proposing v4 vs v7.
2. **Spike**: pick ONE table (`llm_audit_log`, lowest FK coupling) and run Phases A–C on it as a proof-of-concept on a feature branch. Measure: index size delta, query latency p50/p95, all tests still passing.
3. **Decision**: based on the spike, either commit to the full migration or document the spike findings as the rationale for staying with INT ids + the post-seed sync fix.

## Connects to

- The new sequence-sync issue (filed alongside this, the immediate bug fix) — points back here as the durable solution.
- [JIE#181](https://github.com/Building-With-Agents/job-intelligence-engine/issues/181), [JIE#182](https://github.com/Building-With-Agents/job-intelligence-engine/issues/182), [JIE#183](https://github.com/Building-With-Agents/job-intelligence-engine/issues/183) — three earlier siblings of the same root cause.
- DRY-audit memory in `~/.claude/projects/.../memory/project_dry_audit_pending.md` — Gary's noted that divergent code paths are accumulating; UUID migration would consolidate id-handling.

## Labels

`enhancement`, `architecture`, `post-demo`, `P2`, `discuss`
