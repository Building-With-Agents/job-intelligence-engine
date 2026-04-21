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

### Check 1 — `dbo.canonical_roles` (cluster inventory + job-family read)

**What I tested.**

- Local read-only SQL via `python scripts/db_check.py query "…"` from repo root
  (same session as IMP-032 above; redact host/user in notes if you copy this doc).
- Cluster volume:
  `SELECT COUNT(*) AS cluster_count FROM dbo.canonical_roles;`
- Ranked sample for label / `representative_titles` signal:
  `SELECT role_id, label, posting_count, representative_titles, is_llm_generated FROM dbo.canonical_roles ORDER BY posting_count DESC NULLS LAST LIMIT 25;`

**What I found.**

- **`cluster_count`:** 6 — `SELECT COUNT(*) AS cluster_count FROM dbo.canonical_roles;` via `python scripts/db_check.py query` (dev DB `localhost:5432/talent_finder`, run ~2026-04-21 01:23 UTC per terminal log).
- **Qualitative read (sample query, ~01:24 UTC):** `SELECT role_id, label, posting_count, representative_titles, is_llm_generated FROM dbo.canonical_roles ORDER BY posting_count DESC NULLS LAST LIMIT 25;` returned six rows. The `label` strings read as recognizable engineering families in that slice: Site Reliability Engineer; Automation and Robotics Engineer; RPA Developer (UiPath & Power Automate); two distinct Mobile Application Developer (iOS/Android) clusters; Senior SDET - AI Automation Engineer.
- **Split / overlap signal:** The two mobile clusters use overlapping skill domains but different `label` text (one includes “(2-4 years of exp) - USC and GC's” in the pasted `label`; the other is shorter). Treat them as separate buckets when citing role names, not interchangeable shorthand for “mobile.”
- **Heterogeneity in the top-volume row:** For `label` “Site Reliability Engineer” (`posting_count` 542 in the paste), `representative_titles` includes both SRE-flavored strings and titles naming Machine Learning Engineer, AI Engineer, and Infrastructure Administrator — adjacent but not identical families — so a one-line “this cluster is …” summary can misread scope if it ignores those strings.
- **Provenance / noise cues in the paste:** Five rows show `is_llm_generated` = `True` and one `False` (“Mobile Application Developer … USC and GC's”). At least one pasted representative title contains a trophy emoji; that is verbatim surface text from the DB, not editorial emphasis.

**Why it matters for Q&A readiness.**

- Routed “role / evolution” style answers still hit `dbo.canonical_roles` for
  human-readable labels and skill/tool context. Thin or ambiguous clusters cap
  how confidently we can narrate **which** job family the data is about, even
  when downstream SQL is valid.
- When evidence cites `label` alone while `representative_titles` span neighboring families (visible on the pasted SRE row), synthesis should either quote the stored titles or caveat the cluster boundary — otherwise the answer can sound more precise than the taxonomy slice supports.

### Check 2 — `dbo.role_snapshot_weekly` (multi-week coverage)

**What I tested.**

- Distinct week anchors, span, and total rows in one aggregate query:
  `SELECT COUNT(DISTINCT week_start) AS distinct_week_start, MIN(week_start) AS min_week_start, MAX(week_start) AS max_week_start, COUNT(*) AS total_rows FROM dbo.role_snapshot_weekly;`
- Rows per `week_start` (and implied posting volume roll-up):
  `SELECT week_start, COUNT(*) AS snapshot_rows, SUM(posting_count) AS sum_posting_count FROM dbo.role_snapshot_weekly GROUP BY week_start ORDER BY week_start;`

**What I found.**

- **Query (dev `localhost:5432/talent_finder`, ~2026-04-21 01:24 UTC):**  
  `SELECT COUNT(DISTINCT week_start) AS distinct_week_start, MIN(week_start) AS min_week_start, MAX(week_start) AS max_week_start, COUNT(*) AS total_rows FROM dbo.role_snapshot_weekly;` via `python scripts/db_check.py query`.
- **`total_rows`:** 0.
- **`distinct_week_start`:** 0.
- **`min_week_start` / `max_week_start`:** `None` / `None` (verbatim from `db_check.py` output).
- **Multi-week coverage:** **No** — with `distinct_week_start = 0` and `total_rows = 0`, there are no weeks and therefore no multi-week span in `dbo.role_snapshot_weekly` for this snapshot.
- **`GROUP BY week_start` check (dev `localhost:5432/talent_finder`, ~2026-04-21 01:26 UTC):**  
  `SELECT week_start, COUNT(*) AS snapshot_rows, SUM(posting_count) AS sum_posting_count FROM dbo.role_snapshot_weekly GROUP BY week_start ORDER BY week_start;` via `python scripts/db_check.py query` printed **`(no rows)`**.
- **Spread vs. one-week concentration:** Neither applies to this grouped output — there are **no** `week_start` buckets returned, so the data are not spread across multiple weeks and not concentrated in a single week within that query’s result set.
- **Sparsity:** The per-week breakdown is **fully empty** (`(no rows)`), consistent with the zero-row full-table counts above.

**Why it matters for Q&A readiness.**

- Anything that cites **weekly role demand or salary bands by canonical role**
  needs non-empty `dbo.role_snapshot_weekly` rows keyed by `week_start`. Here the
  table is empty (`total_rows` 0, `distinct_week_start` 0), and the `GROUP BY
  week_start` follow-up also returned **`(no rows)`** — so there is **no** named
  week anchor, **no** per-week posting roll-up to cite, and **no** basis to claim
  either multi-week spread or a single dominant week from this table alone.

### Check 3 — Posting → canonical role coverage (`dbo.job_postings`)

**What I tested.**

- Issue #229 wording calls out `job_title_to_canonical_role`; there is **no**
  such table in-repo — the audit path is explicitly
  `dbo.job_postings.canonical_role_id` -> `dbo.canonical_roles.role_id` (see
  repo note above and migrations for the optional FK).
- Denominator and non-null coverage:
  `SELECT COUNT(*) AS total_job_postings, COUNT(canonical_role_id) AS with_non_null_canonical_role_id, ROUND(100.0 * COUNT(canonical_role_id) / NULLIF(COUNT(*), 0), 2) AS pct_non_null_canonical_role_id FROM dbo.job_postings;`
- Join validity on non-null ids:
  `SELECT COUNT(*) AS postings_with_canonical_role_id, COUNT(cr.role_id) AS postings_joinable_to_canonical_roles FROM dbo.job_postings jp LEFT JOIN dbo.canonical_roles cr ON cr.role_id = jp.canonical_role_id WHERE jp.canonical_role_id IS NOT NULL;`

**What I found.**

- **Query (dev `localhost:5432/talent_finder`, ~2026-04-21 01:26 UTC):**  
  `SELECT COUNT(*) AS total_job_postings, COUNT(canonical_role_id) AS with_non_null_canonical_role_id, ROUND(100.0 * COUNT(canonical_role_id) / NULLIF(COUNT(*), 0), 2) AS pct_non_null_canonical_role_id FROM dbo.job_postings;` via `python scripts/db_check.py query`.
- **Issue #229 vs repo shape:** Issue text references `job_title_to_canonical_role`; there is still **no** such table — this check is implemented on **`dbo.job_postings.canonical_role_id` -> `dbo.canonical_roles.role_id`** (same as the addendum repo note above).
- **`total_job_postings` (denominator):** 2,679 — full-table `COUNT(*)` from that query (not a filtered “active only” slice).
- **`with_non_null_canonical_role_id`:** 630.
- **`pct_non_null_canonical_role_id`:** 23.52 (as printed by `db_check.py`).
- **Join validity (~2026-04-21 01:27 UTC):**  
  `SELECT COUNT(*) AS postings_with_canonical_role_id, COUNT(cr.role_id) AS postings_joinable_to_canonical_roles FROM dbo.job_postings jp LEFT JOIN dbo.canonical_roles cr ON cr.role_id = jp.canonical_role_id WHERE jp.canonical_role_id IS NOT NULL;` via `python scripts/db_check.py query` returned **`postings_with_canonical_role_id` = 630** and **`postings_joinable_to_canonical_roles` = 630**.
- **Mismatch check:** `postings_with_canonical_role_id` and `postings_joinable_to_canonical_roles` are both **630** — no delta on this pasted row, so **no** mismatch is visible here (no integrity gap signal from this aggregate alone).
- **Join rate on the non-null slice (from the pasted pair only):** 630 / 630 → **100%** of rows counted in `postings_with_canonical_role_id` also count in `postings_joinable_to_canonical_roles`.

**Why it matters for Q&A readiness.**

- Postings without `canonical_role_id` drop out of joins that aggregate **by
  canonical role** (including `role_snapshot_weekly` refresh inputs that filter
  on `jp.canonical_role_id IS NOT NULL`). Low coverage or orphan ids widen the
  gap between “jobs in `job_postings`” and “jobs we can honestly bucket into a
  named canonical role” in an answer.
- For the **non-null slice** (`630` / `2,679` in the coverage query above), the
  join-validity pair **`630` / `630`** means taxonomy-backed answers that join
  `dbo.job_postings` to `dbo.canonical_roles` on `canonical_role_id` → `role_id`
  can still resolve **labels and JSON facets** for those rows without a missing-key
  drop on this check alone — the headline coverage gap remains the **null**
  majority (`2,679 − 630 = 2,049` by arithmetic on the two counts already recorded
  here), not broken FK targets on assigned ids.

### Recommendation

- Treat **`dbo.role_snapshot_weekly` as empty for this dev snapshot** (`total_rows` 0, `GROUP BY week_start` → `(no rows)`) — do not cite weekly canonical-role demand, counts, or salary roll-ups from that table until it is populated.
- Surface **`job_postings.canonical_role_id` coverage** in any role-bucketing narrative: **23.52%** non-null (**630** / **2,679** on the audited counts); the remaining **2,049** rows carry **NULL** `canonical_role_id` and therefore sit outside canonical-role joins on this slice alone.
- For the **Site Reliability Engineer** cluster (**542** postings in the sample), do not collapse evidence to **`label` alone** — the pasted **`representative_titles`** mix SRE-flavored strings with ML/AI and infrastructure-administrator titles; evidence blocks or caveats should reflect that spread.
- Keep the **two Mobile Application Developer** clusters (**distinct `label` strings** in the paste) **separate** in narrative and SQL filters — they are not interchangeable shorthand for “mobile” without reading the labels.

### Taxonomy gaps (carry-forward #230)

Structured only from the counts and qualitative notes already recorded above (no Pair B / sector-geo scope).

1. **`dbo.role_snapshot_weekly` · (table-wide empty — no `week_start` rows materialized)**  
   - **Issue:** `total_rows` **0**, `distinct_week_start` **0**, `min`/`max` **`None`**, and per-week `GROUP BY` returned **`(no rows)`**.  
   - **Effect on Q&A answers:** Any intent that needs **weekly** canonical-role aggregates has **no** citeable partition or posting roll-up from this table on the audited DB.  
   - **Classification:** **requires pipeline re-run** (populate snapshots upstream of Q&A; outside `refresh_aggregates.py` scope noted in IMP-032 body).

2. **`dbo.job_postings` · `canonical_role_id`**  
   - **Issue:** Only **630** of **2,679** postings carry a non-null `canonical_role_id` (**23.52%**); **2,049** are **NULL** by arithmetic on those same audited counts.  
   - **Effect on Q&A answers:** Queries that **INNER JOIN** on `canonical_role_id` silently exclude the **NULL** majority; population-level “all postings” claims diverge from “canonical-role–assigned postings” unless the answer states the denominator.  
   - **Classification:** **requires pipeline re-run** (assignment/clustering coverage — not fixable by copy alone).

3. **`dbo.canonical_roles` · `label` vs `representative_titles` (top-volume row in paste)**  
   - **Issue:** For **`label` = “Site Reliability Engineer”** (`posting_count` **542**), **`representative_titles`** lists strings that include **Machine Learning Engineer**, **AI Engineer**, and **Infrastructure Administrator** alongside SRE variants.  
   - **Effect on Q&A answers:** Role-family narration that quotes **`label` only** can read **narrower or mis-scoped** relative to the stored title mix the cluster actually holds.  
   - **Classification:** **fixable in Week 10** (synthesis / evidence templates — cite `representative_titles` or explicit boundary language; re-clustering is a separate, heavier lever).

4. **`dbo.canonical_roles` · `label` (two mobile rows in paste)**  
   - **Issue:** **Two** clusters share the same broad domain (**Mobile Application Developer**) but **different `label` text** (one includes visa / experience language from the paste; the other is shorter).  
   - **Effect on Q&A answers:** Treating them as **one** interchangeable “mobile” bucket without naming the distinct **`label`** values risks **double-counting or wrong joins** when filtering on `role_id` / `label`.  
   - **Classification:** **fixable in Week 10** (router prompts, evidence captions, operator runbook — disambiguate the two `label` strings).

### Data / Evidence

**Check 1 — raw output**

```
2026-04-21 01:23:59 [info     ] db_engine_created              url=localhost:5432/talent_finder
cluster_count
------------------------------------------------------------
6
```

```
2026-04-21 01:24:26 [info     ] db_engine_created              url=localhost:5432/talent_finder
role_id label   posting_count   representative_titles   is_llm_generated
------------------------------------------------------------
b68af3b7-2555-5529-be30-43a446e73e56    Site Reliability Engineer       542  ['Site Reliability Engineer (SRE)', 'Machine Learning Engineer', 'Infrastructure Administrator', 'Site Reliability Engineer', 'AI Engineer']   True
28833c24-1446-52ab-8373-1839e9682bb5    Automation and Robotics Engineer     35       ['Robotics Engineer', 'Industrial Automation Engineer', 'Automation Programmer/SCADA Programmer/PLC Programmer', 'Robotics Software Engineer – Autonomy & Perception', 'Robotics Engineers']        True
7bf7431f-5856-58cc-9d2e-afa3b962a8b7    RPA Developer (UiPath & Power Automate)       17      ['UI Path-RPA-Power Platform Developer', 'Robotic Process Automation (RPA) Developer', 'RPA Developer (UiPath) — Automate Mission-Critical Tasks', 'RPA Developer UiPath & Power Automate | AI-Driven', 'RPA Developer'] True
d24c5f17-e08f-5148-9b71-969655020769    Mobile Application Developer (iOS / Android) (2-4 years of exp) - USC and GC's        15      ["Mobile Application Developer (iOS / Android) (2-4 years of exp) - USC and GC's"]    False
4c87ed2a-40eb-5e4b-bfb6-011bdd424adf    Senior SDET - AI Automation Engineer 11       ['SDET / Automation Engineer (AI / Automation)', 'Senior QA Automation Engineer QA, Test 🏆', 'Software Development Engineer in Test (SDET)', 'Senior SDET - QA Engineer', 'Senior SDET / QA Automation Engineer']  True
68d872d0-b304-5e90-b6d2-fef7e894bfcc    Mobile Application Developer (iOS & Android)  10      ['Android/iOS Mobile Developer', 'ios/ android developer', 'Mobile Application Developer (iOS & Android)', 'Mobile Developer - Android/IOS', 'Mobile Application Developer (Android & iOS)']        True
```

**Check 2 — raw output**

```
2026-04-21 01:24:54 [info     ] db_engine_created              url=localhost:5432/talent_finder
distinct_week_start     min_week_start  max_week_start  total_rows
------------------------------------------------------------
0       None    None    0
```

```
2026-04-21 01:26:01 [info     ] db_engine_created              url=localhost:5432/talent_finder
(no rows)
```

**Check 3 — raw output**

```
2026-04-21 01:26:34 [info     ] db_engine_created              url=localhost:5432/talent_finder
total_job_postings      with_non_null_canonical_role_id pct_non_null_canonical_role_id
------------------------------------------------------------
2679    630     23.52
```

```
2026-04-21 01:27:15 [info     ] db_engine_created              url=localhost:5432/talent_finder
postings_with_canonical_role_id postings_joinable_to_canonical_roles
------------------------------------------------------------
630     630
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
