# Post-backfill NULL `quality_score` verification (#328)

**Refs:** #328 — operator fills the table after running the backfill script.

## SQL

```sql
SELECT COUNT(*) AS null_quality_remaining
FROM dbo.job_postings
WHERE quality_score IS NULL
  AND (is_spam IS NOT TRUE);
```

## Acceptance

- **Expected:** `0` after full backfill on the source-of-truth database (or a **small bounded** remainder with written justification — e.g. rows with no `normalized_jobs` join).

## Recorded result

| Environment | Date | `null_quality_remaining` | Operator |
|-------------|------|---------------------------|----------|
| _(fill after run)_ | | | |
