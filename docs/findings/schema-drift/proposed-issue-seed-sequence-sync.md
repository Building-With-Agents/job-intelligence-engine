# bug(seed): post-seed integer-id sequences not synced — first INSERT after drop+reimport collides with id=1

## Problem

After `docker compose down -v` + `docker compose up -d` + `python scripts/pg-seed-data/seed_pg_database.py` (the standard "drop and reimport" workflow), the integer-id sequences for several agent pipeline tables are left at `last_value = 1` (or some small number) while the table's actual `MAX(id)` is in the thousands. The next INSERT from any agent (e.g. `batch_ingest.py`) hits:

```
psycopg2.errors.UniqueViolation: duplicate key value violates unique constraint "raw_ingested_jobs_pkey"
DETAIL:  Key (id)=(1) already exists.
```

Reproduced 2026-05-03 during a JIE#209 cleanup-validation drop+reimport.

## Observed sequence vs MAX(id) gaps after fresh seed

```
raw_ingested_jobs       seq=1    max_id=5268    DRIFT 5267
extracted_intelligence  seq=1    max_id=5207    DRIFT 5206
normalized_jobs         seq=4860 max_id=4860    OK (sync ran at the right time for this one)
llm_audit_log           seq=47884 max_id=47884  OK
normalization_quarantine seq=68  max_id=68      OK
```

Two of the five integer-id agent tables drifted; three were OK. The drift is non-deterministic and depends on the order Step 3 / Step 4 of the seeder hit each table.

## Root cause

`scripts/pg-seed-data/seed_pg_database.py` calls `run_agent_migrations()` at line 433 (Step 2), which invokes `common.data_store.migrations.run_migrations()`, which ends with `_sync_agent_serial_sequences(engine)`. **But this runs BEFORE Step 3 / Step 4 load any data.** With an empty (or minimally-seeded) table, `setval(seq, MAX(id))` becomes `setval(seq, 1)` (the default for an empty table).

After the sync, Steps 3 and 4 use psycopg2 `INSERT ... VALUES (...)` with explicit `id` values from the fixtures. Those inserts do **not** advance the sequence (Postgres sequences are advanced by `nextval()` calls, which only happen with `DEFAULT id` — not when the value is provided explicitly).

End result: data is loaded with explicit ids up to ~5000, sequence stays at 1, next `nextval()` returns 2 (pushing past 1), and the next agent insert collides.

## Affected sequences

Per `_SERIAL_SEQUENCE_TARGETS` in `common/data_store/migrations.py`:

- `raw_ingested_jobs.id`
- `job_ingestion_runs.id` (UUID — not actually a serial sequence; the helper silently skips it)
- `normalized_jobs.id`
- `normalization_quarantine.id`
- `extracted_intelligence.id`
- `llm_audit_log.id`
- `employer_profiles.id`
- `cohort_gap_cache.id`
- `orchestration_audit_log.id`

## Proposed fix

### Minimal change: call `_sync_agent_serial_sequences()` at the END of `seed_database()`

In `scripts/pg-seed-data/seed_pg_database.py`, after Step 4 completes:

```python
# ── Step 5: Re-sync sequences after data loaded ─────────────────
print("\nStep 5: Syncing serial sequences (post-seed)...")
try:
    sys.path.insert(0, str(_REPO_ROOT))
    from common.data_store.database import get_engine
    from common.data_store.migrations import _sync_agent_serial_sequences
    _sync_agent_serial_sequences(get_engine())
    print("  Sequences synced.")
except Exception as exc:
    print(f"  WARNING: sequence sync failed: {exc}")
    print("  Run manually: python -c 'from common.data_store.database import get_engine; from common.data_store.migrations import _sync_agent_serial_sequences; _sync_agent_serial_sequences(get_engine())'")
```

### Defensive change: also call from `seed_agent_data.seed_all()`

If anyone runs the agent-only seeder in isolation, it has the same problem. Mirror the call at the end of `seed_agent_data.seed_all()`.

## Acceptance criteria

- [ ] After `docker compose down -v && docker compose up -d && python scripts/pg-seed-data/seed_pg_database.py`, every integer-id agent table has `last_value(seq) == MAX(id)` for non-empty tables.
- [ ] An immediately-following `python scripts/batch_ingest.py` query INSERT does NOT collide on `pkey`.
- [ ] Regression test: `tests/test_seed_idempotency.py` (or similar) drops + reseeds a small fixture, then asserts an INSERT against each integer-id table works without collision.

## Connects to

- [JIE#181](https://github.com/Building-With-Agents/job-intelligence-engine/issues/181), [JIE#182](https://github.com/Building-With-Agents/job-intelligence-engine/issues/182), [JIE#183](https://github.com/Building-With-Agents/job-intelligence-engine/issues/183) — sibling seed-script bugs, all surfaced during Azure-side seeding work in PR#179.
- Future: a UUID-v4 migration would eliminate this entire class of bug.

## Demo-day implication

Low. The May 6 demo runs against `development @ 369315c` with no drop+reimport in the path. This bug only manifests when the sequence of operations is "down -v → up -d → seed → first ingest". As long as the demo Postgres is left running between now and May 6, this is invisible.

## Labels

`bug`, `P2`, `post-demo` (the fix is in scope now to unblock JIE#209 validation, but the issue's blast radius is post-demo)
