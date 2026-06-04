## Overview

This pull request addresses several Phase 1 cleanup and diagnostic tasks spanning the SOC classification and enrichment pipelines.

## Changes Included

### SOC & Diagnostics
- [x] **SOC Silent-Fallback Fix (P0):** Elevates the logging level to `WARN` at the individual-record level and adds a batch-level **degrade-and-alert guardrail** (`_check_soc_unclassified_rate`) that fires when the `unclassified` rate exceeds the configured threshold (default 10%). The batch continues processing — this is intentional "degrade and alert" behavior, not a hard stop. An `EnrichmentDegraded` event is published to the alert bus so the Orchestration Agent can page on-call if needed.

### DRY Refactoring
- [x] **SQL UPDATE Consolidation (P1):** Unifies the three duplicate SQL `UPDATE` templates within `job_postings_promotion.py` into a single parameterized template.
- [x] **Sync/Async Duplicate Extraction (P2):** Extracts the shared `_build_employer_profile_impl()` logic in `employer_classifier.py` to resolve code duplication between synchronous and asynchronous paths.

## Verification
- [x] Targeted test suite run (`pytest enrichment/tests/ -v`, `pytest tests/test_enrichment_agent.py -v`)
- [x] Full suite run (`pytest -x`)
- [x] Smoke-tested affected code paths (`scripts/smoke_soc_fallback.py`)

## Note on "degrade and alert" vs "circuit breaker"

> Responding to Lou's review: the SOC threshold check intentionally logs `soc_unclassified_rate_exceeded`, publishes `EnrichmentDegraded`, and **continues processing the batch**. This is degrade-and-alert behavior — the batch is not halted. A true circuit breaker (open/half-open/closed state machine that stops traffic) is a Phase 2 item in `orchestration/circuit_breaker/`. The PR description has been updated to reflect this accurately.
