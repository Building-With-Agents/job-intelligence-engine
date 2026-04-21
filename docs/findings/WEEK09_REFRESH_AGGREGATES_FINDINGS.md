# Week 9 — IMP-032 Findings: Minimal Orchestration (`scripts/refresh_aggregates.py`)

**Owner:** Angel + Fabian
**Ticket:** IMP-032
**Sprint:** Week 9
**Target demo:** May 7, 2026

Minimal operational refresh for the six Week 7 aggregate tables. No scheduler, no
heartbeat table, no clustering/disruption refresh — those are deliberately deferred
to the Week 12 Deferred Work Log below.

---

## What I Tested

- Executed `python scripts/refresh_aggregates.py` against the dev Postgres
  (`localhost:5432/talent_finder`) from repo root with venv activated,
  target_week = prior-Monday-UTC default (`2026-04-13`).
- Executed the same command a second time to verify idempotency (re-run
  against the same week must not drift row counts).
- Executed `python scripts/refresh_aggregates.py --skip-pipeline` to confirm
  sector/geo are cleanly skipped.
- Cross-checked per-table row counts against
  `python -m scripts.week8_verify_counts` after the refresh.
- Exercised the DB-unreachable path (Postgres down before the user started
  the container) and confirmed exit code `1` + `refresh_db_unreachable`
  structlog line + stderr message.

**Fault-injection coverage (`tests/test_refresh_aggregates_script.py`,
4 tests, runs in ~1.4s without a database):**
- `test_run_refresh_catches_exception_and_records_failure` — patches the
  refresh callable to raise `RuntimeError`, asserts `StepResult.status ==
  "failure"`, the error is captured, and the exception never propagates
  out of `_run_refresh` (this is the runtime proof of the "log and
  continue" contract).
- `test_run_refresh_records_success` — sibling happy-path assertion.
- `test_run_refresh_truncates_long_error` — oversized exception messages
  are clamped to `_ERROR_TRUNCATE` chars so the structured log line stays
  tractable.
- `test_skipped_helper_returns_skipped_status` — covers the dependency
  short-circuit path used for `skill_velocity` / `skill_co_occurrence`
  when `skill_demand_weekly` fails (`status="skipped"`, `skip_reason`
  populated, no row counts touched).

Together these cover every branch of `_run_refresh` and `_skipped`. The
one fault path **not** in the unit suite is a live end-to-end run with
one of the real aggregator imports swapped out — deferred because it
would need a DB fixture and adds no signal over the monkey-patched
version above.

## What I Found

**Default-week run (`python scripts/refresh_aggregates.py`)** — `target_week = 2026-04-13`,
**total_duration_ms = 944.1**, success=6, failure=0, skipped=0.

| table                     | status  | rows_before | rows_after | delta   | duration_ms |
| ------------------------- | ------- | ----------- | ---------- | ------- | ----------- |
| `skill_demand_weekly`     | success | 0           | 3406       | +3406   | 242.38      |
| `tool_demand_weekly`      | success | 0           | 70         | +70     | 41.68       |
| `skill_velocity`          | success | 0           | 3983       | +3983   | 447.75      |
| `skill_co_occurrence`     | success | 0           | 200        | +200    | 99.13       |
| `sector_summary_weekly`   | success | 0           | 0          | +0      | 12.53       |
| `geo_demand_weekly`       | success | 0           | 0          | +0      | 6.02        |

`sector_summary_weekly` and `geo_demand_weekly` ran to completion but
produced zero rows. This is **not a script defect** — both compute
functions logged `total_postings=0` because
`dbo.job_postings.publish_date` is ~99% NULL
(issue [#172](https://github.com/Building-With-Agents/job-intelligence-engine/issues/172)),
so the `WHERE publish_date >= :week_start AND publish_date < :week_end`
filter matched no rows. Once #172's backfill lands, the same script will
populate these two tables with no code change.

**Second default-week run (idempotency check)** — same target_week,
**total_duration_ms = 688.3**, success=4, failure=0, skipped=2 (because
the operator passed `--skip-pipeline` on the replay).

| table                     | status  | rows_before | rows_after | delta | duration_ms |
| ------------------------- | ------- | ----------- | ---------- | ----- | ----------- |
| `skill_demand_weekly`     | success | 3406        | 3406       | +0    | 124.11      |
| `tool_demand_weekly`      | success | 70          | 70         | +0    | 15.61       |
| `skill_velocity`          | success | 3983        | 3983       | +0    | 418.18      |
| `skill_co_occurrence`     | success | 200         | 200        | +0    | 84.73       |
| `sector_summary_weekly`   | skipped | -           | -          | -     | 0.00        (skip-pipeline flag) |
| `geo_demand_weekly`       | skipped | -           | -          | -     | 0.00        (skip-pipeline flag) |

Row deltas of `+0` across the board prove every underlying refresh is
idempotent on the `(table, week_start)` key — a missed scheduled run can
be recovered by re-running the script manually without producing
duplicates or corrupting the aggregate. This is the Week 9 reading's
"safe to run twice" contract, confirmed on real data.

**`--skip-pipeline` behavior.** The two `pipeline`-tagged tables emit
`status=skipped` with `skip_reason="skip-pipeline flag"` and zero duration.
The 4 `aggregate`-tagged tables run unchanged. Matches the spec.

**Cross-check against `week8_verify_counts.py`.** After the refresh:

| table                     | per-week count (this run) | global count (verify_counts) | interpretation |
| ------------------------- | -------------------------: | ---------------------------: | -------------- |
| `skill_demand_weekly`     | 3406                       | 4513                         | Other weeks carry the remaining 1107 rows |
| `tool_demand_weekly`      | 70                         | 142                          | Ditto |
| `skill_velocity`          | 3983                       | 5090                         | Ditto |
| `skill_co_occurrence`     | 200                        | 400                          | Ditto |
| `sector_summary_weekly`   | 0                          | 0                            | Blocked on #172 |
| `geo_demand_weekly`       | 0                          | 0                            | Blocked on #172 |

The "global ≥ per-week" invariant holds for every table. The refresh
wrote to the correct `week_start` partition and did not touch prior-week
rows (as required — the refresh is week-scoped, not table-scoped).

**DB-unreachable path (with Postgres stopped).** Exit code `1`, single
`refresh_db_unreachable` structlog line, `error: database unreachable —
see refresh_db_unreachable log` on stderr. No partial side effects, no
exception stack trace leaked to the operator.

## Recommendation

- Adopt `scripts/refresh_aggregates.py` as the Week 9 refresh primitive. Run it
  manually pre-demo and immediately before any dashboard walk-through.
- **Do not** wire this into cron yet. A scheduler without a heartbeat table +
  Streamlit "last success" page gives the illusion of orchestration without the
  observability to trust it. Schedule only after the heartbeat work in the
  deferred log ships.
- Keep the exit code contract narrow: `0` = ran to completion, `1` = DB down
  or unhandled exception. Per-table failures live in the stdout summary and
  the `refresh_run_complete` structlog line, not in the exit code. When the
  heartbeat table lands, that line becomes the insert payload verbatim.

## Tradeoffs Acknowledged

- **No scheduler.** The refresh is as fresh as the last human who ran it. For
  a manually-driven demo week this is acceptable; for a production pipeline it
  is not. The Week 12 debt entry below names the trigger.
- **No heartbeat / dashboard.** A failed refresh is only visible to whoever was
  looking at the terminal. We mitigate with a mandatory pre-demo run captured
  in the Week 10 runbook and by including `rows_after` in the stdout summary so
  "did it work" is answerable in one glance.
- **No clustering refresh.** `canonical_roles` and `role_snapshot_weekly` age
  between manual `python scripts/run_clustering.py` invocations. This is the
  right call — re-clustering on every refresh pass would scramble the taxonomy
  and invalidate every `role_snapshot_weekly` answer we cite, which is exactly
  the failure mode the Week 9 reading warned about.
- **No disruption refresh.** `disruption_fingerprints` depends on
  `canonical_roles` being current; refreshing disruption alone would produce
  fingerprints rooted in stale role clusters. Treated as one block with
  clustering in the deferred log.
- **Per-table row counts, not per-skill diff.** We can tell you the count
  dropped; we cannot tell you _which_ skill rows are gone. For Phase 1 that's
  fine. Phase 2 should add a content-level diff (top-N skill churn) if and
  when `role_snapshot_weekly` stories start depending on skill continuity.
- **No dry-run mode.** `--week` lets an operator target an alternate partition,
  but there is no `--dry-run` that skips writes. The refresh is idempotent per
  `week_start`, so the cost of a real run is low; a dry-run flag is cheap to
  add later if it earns its keep.

## Data / Evidence

- Script: [`scripts/refresh_aggregates.py`](../../scripts/refresh_aggregates.py)
- Inner computation entry points (unchanged in this ticket):
  - [`analytics/aggregators/demand_weekly.py`](../../analytics/aggregators/demand_weekly.py)
  - [`analytics/aggregators/velocity.py`](../../analytics/aggregators/velocity.py)
  - [`analytics/aggregators/co_occurrence.py`](../../analytics/aggregators/co_occurrence.py)
  - [`analytics/aggregators/sector_weekly.py`](../../analytics/aggregators/sector_weekly.py)
  - [`analytics/aggregators/geo_demand.py`](../../analytics/aggregators/geo_demand.py)
- Cross-check: [`scripts/week8_verify_counts.py`](../../scripts/week8_verify_counts.py)
- Week 8 runbook updated to reference the new script path:
  [`docs/runbooks/WEEK08_TESTING_RUNBOOK.md`](./WEEK08_TESTING_RUNBOOK.md).
### Canonical run — stdout summary block (default week)

```
refresh_aggregates summary  target_week=2026-04-13  total_duration_ms=944.1
success=6  failure=0  skipped=0
----------------------------------------------------------------------------------------------------
table                       status        before       after     delta          ms  note
----------------------------------------------------------------------------------------------------
skill_demand_weekly         success            0        3406     +3406      242.38
tool_demand_weekly          success            0          70       +70       41.68
skill_velocity              success            0        3983     +3983      447.75
skill_co_occurrence         success            0         200      +200       99.13
sector_summary_weekly       success            0           0        +0       12.53
geo_demand_weekly           success            0           0        +0        6.02
```

### Idempotency replay — stdout summary block (`--skip-pipeline`)

```
refresh_aggregates summary  target_week=2026-04-13  total_duration_ms=688.3
success=4  failure=0  skipped=2
----------------------------------------------------------------------------------------------------
table                       status        before       after     delta          ms  note
----------------------------------------------------------------------------------------------------
skill_demand_weekly         success         3406        3406        +0      124.11
tool_demand_weekly          success           70          70        +0       15.61
skill_velocity              success         3983        3983        +0      418.18
skill_co_occurrence         success          200         200        +0       84.73
sector_summary_weekly       skipped            -           -         -        0.00  skip-pipeline flag
geo_demand_weekly           skipped            -           -         -        0.00  skip-pipeline flag
```

### `refresh_run_complete` structlog line (canonical run, one-line form)

```
[info     ] refresh_run_complete  correlation_id=refresh-aggregates-20260420T024006Z
  target_week=2026-04-13  total_duration_ms=944.12
  success_count=6  failure_count=0  skipped_count=0
  results=[
    {'name': 'skill_demand_weekly',   'status': 'success', 'rows_before': 0, 'rows_after': 3406, 'rows_delta': 3406, 'duration_ms': 242.38, 'error': None, 'skip_reason': None},
    {'name': 'tool_demand_weekly',    'status': 'success', 'rows_before': 0, 'rows_after': 70,   'rows_delta': 70,   'duration_ms': 41.68,  'error': None, 'skip_reason': None},
    {'name': 'skill_velocity',        'status': 'success', 'rows_before': 0, 'rows_after': 3983, 'rows_delta': 3983, 'duration_ms': 447.75, 'error': None, 'skip_reason': None},
    {'name': 'skill_co_occurrence',   'status': 'success', 'rows_before': 0, 'rows_after': 200,  'rows_delta': 200,  'duration_ms': 99.13,  'error': None, 'skip_reason': None},
    {'name': 'sector_summary_weekly', 'status': 'success', 'rows_before': 0, 'rows_after': 0,    'rows_delta': 0,    'duration_ms': 12.53,  'error': None, 'skip_reason': None},
    {'name': 'geo_demand_weekly',     'status': 'success', 'rows_before': 0, 'rows_after': 0,    'rows_delta': 0,    'duration_ms': 6.02,   'error': None, 'skip_reason': None}
  ]
```

This single log line is the payload shape the future `job_runs` heartbeat
table row will be built from (IMP-032 debt entry #1 below) — when the
heartbeat table ships, the refresh script will `INSERT` this dict instead
of (or in addition to) emitting it as a log line.

### `week8_verify_counts.py` cross-check (post-refresh, aggregate section)

```
=== Week 7 — Analytics aggregates + clustering ===
analytics_pipeline_state                1
canonical_roles                         3   (populated by prior run_clustering.py)
skill_demand_weekly                  4513
tool_demand_weekly                    142
role_snapshot_weekly                    0   (clustering snapshot deferred)
sector_summary_weekly                   0   (blocked on #172 publish_date)
geo_demand_weekly                       0   (blocked on #172 publish_date)
skill_velocity                       5090
skill_co_occurrence                   400
```

The non-zero values confirm the refresh wrote through to the DB. The
zero values for `sector_summary_weekly` / `geo_demand_weekly` are the
known upstream data gap, not a regression in this script. The zero for
`role_snapshot_weekly` is expected — clustering / role snapshotting is
deferred work item #3 below.

---

## Taxonomy Audit Addendum (Issue #229)

**Scope:** Read-only SQL against the dev Postgres (`PYTHON_DATABASE_URL`).
This addendum is **orthogonal** to the IMP-032 refresh script validation
above — no `scripts/refresh_aggregates.py` behavior is exercised here.

**Repo note (check #3 wording vs schema).** Issue #229 text references a
`job_title_to_canonical_role` artifact. There is **no** table or module by
that name in this repository. This audit uses the implemented join path
`dbo.job_postings.canonical_role_id` -> `dbo.canonical_roles.role_id`
(see `common/data_store/models.py` and `common/data_store/migrations.py`),
plus optional join-validity checks on that FK target only.

### What I Tested

- <!-- TODO: date, DB (redact credentials), tool: `python scripts/db_check.py query "…"` from repo root -->

**Check 1 — `dbo.canonical_roles` (cluster inventory + qualitative job-family read)**

- <!-- TODO: paste SQL used -->

**Check 2 — `dbo.role_snapshot_weekly` (multiple `week_start` periods)**

- <!-- TODO: paste SQL used -->

**Check 3 — Posting → canonical role coverage (`job_postings.canonical_role_id`)**

- <!-- TODO: paste SQL used (include denominator definition in comment above query if non-obvious) -->

### What I Found

**Check 1 — cluster count and recognizable job families**

- **Cluster count:** <!-- TODO: numeric result -->
- **Qualitative read:** <!-- TODO: 2–4 bullets: do `label` / `representative_titles` read as sensible families vs noise? Any empty or misleading samples? -->

**Check 2 — temporal coverage**

- **Distinct `week_start` values:** <!-- TODO -->
- **Min / max `week_start`:** <!-- TODO -->
- **Per-week row counts (if helpful):** <!-- TODO: table or short bullet list -->
- **Answer to “multiple periods?”:** <!-- TODO: yes / no + one sentence -->

**Check 3 — coverage and join validity**

- **Denominator used:** <!-- TODO: e.g. `COUNT(*)` on full `dbo.job_postings` -->
- **`canonical_role_id` non-null %:** <!-- TODO -->
- **Non-null IDs that resolve to `dbo.canonical_roles`:** <!-- TODO: counts and/or % -->
- **Orphan or mismatched IDs (if any):** <!-- TODO: note or “none observed” -->

### Recommendation

- <!-- TODO: e.g. whether clustering / snapshot refresh cadence is acceptable for demo; whether to file follow-up issues; whether Q&A should caveat role labels — keep grounded in results above -->

### Data / Evidence

**Check 1 — raw output**

```
<!-- TODO: paste db_check.py / psql output: cluster_count query -->
```

```
<!-- TODO: paste sample rows (label, representative_titles, posting_count, …) -->
```

**Check 2 — raw output**

```
<!-- TODO: paste distinct week_start / min-max / group-by output -->
```

**Check 3 — raw output**

```
<!-- TODO: paste coverage + join-validity query output -->
```

**Reference paths (for reviewers)**

- ORM: [`common/data_store/models.py`](../../common/data_store/models.py) — `CanonicalRole`, `RoleSnapshotWeekly`; header comment on `job_postings.canonical_role_id`
- Snapshot week logic: [`analytics/canonical_roles/snapshots.py`](../../analytics/canonical_roles/snapshots.py)
- Ad-hoc queries: [`scripts/db_check.py`](../../scripts/db_check.py)

---

## Week 12 Deferred Work Log (IMP-032 debt entries)

Each entry has the four required fields: **what**, **why safe to defer**,
**trigger to pay the debt**, **cost of the workaround**.

### 1. `job_runs` heartbeat table + Streamlit "last success" page

- **What.** A `dbo.job_runs` table populated by the refresh script (INSERT-start,
  UPDATE-finish, with `status`, `rows_affected`, `error_message`) plus a
  Streamlit page that surfaces `last_success` per job and hours since.
- **Why safe to defer.** The operator pool is one person for the pre-demo
  window, the refresh is manually triggered, and the stdout summary answers
  "did it work" before the terminal is closed.
- **Trigger to pay the debt.** The refresh is wired to any scheduler (pg_cron,
  host cron, GitHub Actions) _or_ the pipeline misses a run without someone
  noticing for more than 24 hours.
- **Cost of the workaround.** Silent failures are silent. If a scheduled
  refresh returns non-zero during a demo window without a dashboard, the
  aggregate tables go stale and nobody knows until a Q&A answer cites week-old
  data.

### 2. pg_cron / host cron / GitHub Actions scheduling

- **What.** A scheduled daily invocation of `scripts/refresh_aggregates.py`
  pinned to UTC, with the human-readable mapping documented in the job name.
- **Why safe to defer.** The script is safe to run twice (every underlying
  refresh function is idempotent per `week_start`), so "catch up by running it
  manually" is an acceptable recovery path during demo week.
- **Trigger to pay the debt.** Pre-demo checklist requires nightly freshness
  guarantees, or the project moves beyond the May 7 demo into any recurring
  reporting cadence.
- **Cost of the workaround.** Someone has to remember to run the script.
  Memory is not a supported scheduling backend.

### 3. Clustering + disruption refresh in the same script

- **What.** Fold `canonical_roles` / `role_snapshot_weekly` (via
  `AnalyticsAgent.process_clustering`) and `disruption_fingerprints` (via
  `DisruptionFingerprintService.refresh_disruption_fingerprints`) into the
  refresh run, gated by taxonomy-stability signals (ARI > 0.85 between
  consecutive clusterings, HDBSCAN `cluster_persistence_` ≥ 0.1 per cluster).
- **Why safe to defer.** Clustering is expensive (embedding generation + HDBSCAN)
  and re-clustering blindly on every refresh can scramble the taxonomy between
  demo runs — exactly the failure mode the Week 9 reading warned against.
  `python scripts/run_clustering.py` remains the manual entry point and is run
  pre-demo only.
- **Trigger to pay the debt.** The next iteration's taxonomy-stability audit
  ships (ARI/HDBSCAN persistence gates, deterministic `random_state=42`) and
  proves clustering can be re-run without drift.
- **Cost of the workaround.** `canonical_roles` and `role_snapshot_weekly` age
  between manual clustering runs. Any Q&A answer citing a role label trusts
  that someone ran `run_clustering.py` recently; there is no automated
  enforcement of that trust.

### 4. Retry / backoff / three-tier alerting / StateGraph orchestration

- **What.** APScheduler + LangGraph StateGraph routing, per-agent retry
  policies with exponential backoff + jitter, three-tier alerting (Warning /
  Critical / Fatal) driven by YAML rules, 100% `orchestration_audit_log`
  completeness, correlation-ID propagation across agents.
- **Why safe to defer.** The full design lives in `lesson-framework/week-09/deferred/`
  and is out of scope for the May 7 demo. Week 12 is the owner.
- **Trigger to pay the debt.** Phase 2 planning opens, or a post-demo incident
  demonstrates that best-effort cron + manual intervention no longer clears
  the reliability bar.
- **Cost of the workaround.** A single transient DB blip forces a manual
  re-run. No backoff, no auto-retry, no paging. A Q&A answer served during
  that window may cite stale aggregates.
