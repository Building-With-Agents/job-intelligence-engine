# v2.1 — Embedding router for canonical role resolution (local verification)

**Run date:** April 28, 2026  
**Issue:** [#229](https://github.com/Building-With-Agents/job-intelligence-engine/issues/229) — taxonomy audit + pgvector `label_embedding` on `dbo.canonical_roles`, similarity routing for role filters; [#292](https://github.com/Building-With-Agents/job-intelligence-engine/issues/292) acceptance (intent_accuracy toward ≥ 0.70 on v2.x eval) deferred to a Langfuse-backed eval run.

---

## Issue #229 goal (current scope)

- Add `dbo.canonical_roles.label_embedding vector(1536)` (pgvector), populated from `cluster_centroid` and kept in sync on each clustering persist.
- Route `role_names` entity filters through embedding similarity (`<=>`) where applicable, with ILIKE fallback when resolution returns no IDs.
- No HNSW index for under 100 roles; no synonym maps; no ORM mapping of the vector column.

---

## Files changed (implementation summary)

| Area | Path | Change |
|------|------|--------|
| Migration DDL | `common/data_store/migrations.py` | Idempotent `ALTER TABLE ... label_embedding vector(1536)` wired like other `_*_ALTER_STATEMENTS` blocks. |
| Backfill | `scripts/backfill_label_embeddings.py` | One-shot `cluster_centroid::text::vector` backfill with `jsonb_array_length = 1536` guard; exits if column missing. |
| Persist | `analytics/canonical_roles/persist.py` | After `session.flush()`, raw `UPDATE ... SET label_embedding = CAST(:vec AS vector)` per cluster with `centroid_embedding`; `label_embeddings_synced` in persist log. |
| Router | `analytics/query_engine/router.py` | `_resolve_role_names_to_canonical_ids` using `_embed_texts_azure` + raw SQL `<=>`; `_route_role_evolution` and `_route_workflow` use `role_id IN (...)` when IDs resolve, else ILIKE on `label`. |
| Tests | `tests/test_label_embedding_migration.py`, `tests/test_canonical_roles_wiring.py`, `analytics/tests/test_router.py` | Postgres migration column check; persist SQL shape; mocked embed tests + monkeypatch on existing router tests that pass `role_names`. |

---

## Before state (local / audit)

- **canonical_roles:** six rows; **`label_embedding` did not exist** until migration.
- **Schema mistakes:** ad-hoc queries assumed `role_snapshot_weekly.snapshot_date` and `skill_demand_weekly.period_start`; **`week_start` is the correct temporal column** on both tables (ORM-aligned).
- **Router:** `role_evolution` and `workflow` filtered `canonical_roles.label` with ILIKE only.

---

## After state (final verification SQL, April 28, 2026)

Executed:

```bash
python scripts/db_check.py query "SELECT COUNT(*) AS total_roles, COUNT(label_embedding) AS with_embedding FROM dbo.canonical_roles"
python scripts/db_check.py query "SELECT COUNT(*) AS total_postings, COUNT(canonical_role_id) AS with_role FROM dbo.job_postings"
python scripts/db_check.py query "SELECT MIN(week_start) AS earliest_week, MAX(week_start) AS latest_week, COUNT(DISTINCT week_start) AS distinct_weeks FROM dbo.role_snapshot_weekly"
python scripts/db_check.py query "SELECT MIN(week_start) AS earliest_week, MAX(week_start) AS latest_week, COUNT(DISTINCT week_start) AS distinct_weeks FROM dbo.skill_demand_weekly"
```

| Check | Result |
|-------|--------|
| `total_roles` / `with_embedding` | **22** / **22** (100% embedded) |
| `job_postings` total / `with canonical_role_id` | **2679** / **856** |
| `role_snapshot_weekly` earliest / latest / distinct `week_start` | **NULL** / **NULL** / **0** (table empty) |
| `skill_demand_weekly` earliest / latest / distinct `week_start` | **2026-03-02** / **2026-03-23** / **4** |

---

## Re-clustering command

```bash
CLUSTER_MIN_CLUSTER_SIZE=5 CLUSTER_MIN_SAMPLES=2 CLUSTER_MIN_TOTAL_POSTINGS=100 \
  .venv/bin/python scripts/run_clustering.py --min-postings 100
```

- HDBSCAN produced **22** clusters on the run referenced for this doc; orphan cleanup left **22** `canonical_roles` rows, all with **`label_embedding`** populated (persist log: **`label_embeddings_synced=22`**).
- **`role_snapshot_weekly_refreshed row_count=0`** for the computed `week_start` (salary percentile path reported zero groups); **`role_snapshot_weekly` remains a data/pipeline gap** on this database.

---

## Tests and lint (local)

- **Pytest:** 42 passed (user-reported aggregate for migration + wiring + router suites).
- **Ruff:** format/check clean on touched test and router files (user-reported).

---

## Notes

- **LangSmith 401** warnings during clustering (multipart ingest) were **non-blocking**; embeddings and persist completed.
- **`trend` / `disruption`:** code inspection showed **no** `role_names` ILIKE on `canonical_roles`; only **`role_evolution`** and **`workflow`** were updated for embedding resolution.
- **`data/analytics/clustering_findings.json`:** script output from clustering runs; not required for this findings doc unless the team tracks it in git.

---

## Next steps (out of scope for this doc)

- Run full **v2.x golden eval** on Langfuse and record composite / `intent_accuracy` vs baseline; link run URL in `eval/qa_prompt_iteration_log.md`.
- Investigate **`role_snapshot_weekly` population** (empty after refresh) when salary or posting filters block inserts.
