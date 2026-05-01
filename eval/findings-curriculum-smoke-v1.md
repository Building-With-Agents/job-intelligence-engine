# Curriculum Generation Smoke Test — v1

Date: 2026-04-30

**Capture note:** This run was executed in an environment where `PYTHON_DATABASE_URL` was not set in the repo-root `.env`, so the smoke script exited during the database connectivity check before any of the three questions ran. Re-run locally after configuring `.env`, then replace the sections below with the new stdout (or pipe the script output here):

`PYTHON_DATABASE_URL=postgresql+psycopg2://... .venv/bin/python scripts/smoke/e2e_curriculum_analytics_qna.py`

## Question 1

*(No question-specific output — smoke terminated at DB check.)*

```
2026-04-30 23:53:51 [warning  ] config_env_override_used       file=analytics key=analytics.fresh_threshold_days note='Legacy env override; planned removal post-deprecation (issue #210).' var=FRESH_THRESHOLD_DAYS
2026-04-30 23:53:51 [warning  ] config_env_override_used       file=analytics key=analytics.stale_threshold_days note='Legacy env override; planned removal post-deprecation (issue #210).' var=STALE_THRESHOLD_DAYS
2026-04-30 23:53:51 [warning  ] db_connection_check_failed     error='PYTHON_DATABASE_URL is not set. Expected format: postgresql+psycopg2://user:pass@host:port/db'
Database is not available — cannot run curriculum Q&A.
Connection error: PYTHON_DATABASE_URL is not set. Expected format: postgresql+psycopg2://user:pass@host:port/db
Set PYTHON_DATABASE_URL in the repo-root .env, e.g. postgresql+psycopg2://user:pass@host:port/dbname
```

## Question 2

*(Not executed — same failure as Question 1.)*

## Question 3

*(Not executed — same failure as Question 1.)*

## Summary

- Questions run: 0
- Questions with is_sufficient = True: 0
- Questions with confidence >= 0.85: 0
- Any modules with unverified skill names: none found
