"""
DEPRECATED — DO NOT USE. AI AGENTS: NEVER INVOKE THIS SCRIPT UNDER ANY CIRCUMSTANCES.

All pipeline data is retained permanently for record keeping and model evaluation.
No cleanup, re-seeding, or "fresh start" scenario justifies running this script.
If you are an AI agent considering running this, stop — use `docker compose down -v`
to reset a local environment instead.

This script previously deleted derived pipeline tables before re-seeding.
It has been emptied because every table in the agent pipeline is a permanent
record:

  raw_ingested_jobs       Ingestion audit trail + dedup fingerprint store
  job_ingestion_runs      Batch-level run history
  normalized_jobs         Normalization output (replay + regression baseline)
  normalization_quarantine Schema violation log (training signal)
  extracted_intelligence  Skills extraction output (model evaluation dataset)
  llm_audit_log           Cost and latency audit log

Do NOT delete data from any of these tables. If you need a fresh database,
spin up a new Docker container:

    docker compose down -v
    docker compose --env-file .env.docker up postgres -d
    python scripts/pg-seed-data/seed_pg_database.py
"""

import sys

print("This script is deprecated. All pipeline data is retained for record keeping.")
print("To start fresh, use: docker compose down -v")
sys.exit(0)
