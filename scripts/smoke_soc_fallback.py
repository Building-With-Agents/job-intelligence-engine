"""Manual smoke test — SOC silent-fallback fix (JIE week-12/p1-unify-sql-updates).

Monkey-patches the SOC LLM to always return "INVALID_CODE" and directly exercises
the three observable points added by the fix:

  SIGNAL 1 — soc_classifier_llm_resolution  (log.warning, inside soc_classifier.py)
             Fires once per record when the LLM picks a code not in the candidate set.

  SIGNAL 2 — enrich_record_soc_unclassified (log.warning, inside agent.enrich_record)
             Fires once per record at the agent boundary.

  SIGNAL 3 — soc_unclassified_rate_exceeded (log.warning + EnrichmentDegraded event)
             Fires once per batch when the unclassified fraction exceeds the threshold.

Run from the repo root with the venv active:

    python scripts/smoke_soc_fallback.py

No PYTHON_DATABASE_URL or Azure OpenAI credentials are required.
Exit code 0 = all assertions green.  Exit code 1 = at least one failure.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env", override=False)

# Fix threshold at 10 % so the smoke test is deterministic regardless of local .env.
os.environ["SOC_UNCLASSIFIED_RATE_THRESHOLD"] = "0.10"

import structlog.testing

from enrichment.agent import EnrichmentAgent, _check_soc_unclassified_rate, register_alert_bus
from enrichment.classifiers.soc_classifier import classify_soc

_FAKE_CANDIDATES = [
    {"code": "15-1252", "title": "Software Developers"},
    {"code": "11-9021", "title": "Computer and Information Systems Managers"},
]

print("\n=== SOC fallback smoke test — INVALID_CODE monkey-patch ===\n")

errors: list[str] = []


# ---------------------------------------------------------------------------
# SIGNAL 1: soc_classifier_llm_resolution warning fires inside classify_soc
# ---------------------------------------------------------------------------

print("--- Signal 1: classify_soc with INVALID_CODE LLM response ---")

with (
    patch(
        "enrichment.classifiers.soc_classifier.get_soc_candidates",
        new=AsyncMock(return_value=_FAKE_CANDIDATES),
    ),
    structlog.testing.capture_logs() as cap1,
):
    result = asyncio.run(
        classify_soc(
            title="Software Engineer",
            description="Build cloud-native systems.",
            session=None,  # session not needed — get_soc_candidates mocked
            llm=lambda _: "INVALID_CODE",
        )
    )

signal1_logs = [
    e for e in cap1 if e.get("log_level") == "warning" and e.get("event") == "soc_classifier_llm_resolution"
]

print(f"  classify_soc return value        : {result!r}  (expected 'unclassified')")
print(f"  soc_classifier_llm_resolution    : {len(signal1_logs)} warning(s)  (expected 1)")

if result != "unclassified":
    errors.append(f"FAIL  classify_soc should return 'unclassified', got {result!r}")
else:
    print("  PASS  return value")

if len(signal1_logs) != 1:
    errors.append(f"FAIL  Expected 1 soc_classifier_llm_resolution warning, got {len(signal1_logs)}")
else:
    rr = signal1_logs[0].get("resolution_reason")
    print(f"  PASS  log.warning fired  (resolution_reason={rr!r})")


# ---------------------------------------------------------------------------
# SIGNAL 2: enrich_record_soc_unclassified warning fires inside enrich_record
# ---------------------------------------------------------------------------

print("\n--- Signal 2: enrich_record with a session that returns 'unclassified' from SOC ---")

_mock_session = MagicMock()

# Patch the external dependencies that run when session is not None so the test
# stays DB-free.  classify_soc is patched to return "unclassified" directly,
# simulating the outcome of an INVALID_CODE LLM response.
from common.types.job_profile import EmployerProfile
from enrichment.resolvers.company_resolver import resolve_company as _rc  # noqa: F401
from enrichment.resolvers.location_resolver import resolve_location as _rl  # noqa: F401

agent2 = EnrichmentAgent()

with (
    patch("enrichment.agent.resolve_company", return_value=("company-id-123", 0.9)),
    patch("enrichment.agent.resolve_location", return_value=(None, 0.5, "El Paso, TX", "el_paso")),
    patch("enrichment.agent.classify_naics", return_value="541511"),
    patch(
        "enrichment.agent.classify_soc",
        new=AsyncMock(return_value="unclassified"),
    ),
    patch("enrichment.agent.build_employer_profile", return_value=EmployerProfile()),
    patch("enrichment.agent.persist_employer_metadata"),
    patch("enrichment.agent.run_coroutine", side_effect=lambda coro: asyncio.run(coro)),
    structlog.testing.capture_logs() as cap2,
):
    enriched = agent2.enrich_record(
        posting={
            "title": "Software Engineer",
            "company": "Acme Corp",
            "description": "Build cloud-native systems.",
            "normalized_job_id": 42,
            "source": "jsearch",
            "external_id": "ext-001",
        },
        session=_mock_session,
    )

signal2_logs = [
    e for e in cap2 if e.get("log_level") == "warning" and e.get("event") == "enrich_record_soc_unclassified"
]

print(f"  enriched['soc_code']             : {enriched.get('soc_code')!r}  (expected None)")
print(f"  enrich_record_soc_unclassified   : {len(signal2_logs)} warning(s)  (expected 1)")

if enriched.get("soc_code") is not None:
    errors.append(f"FAIL  enriched['soc_code'] should be None when unclassified, got {enriched.get('soc_code')!r}")
else:
    print("  PASS  soc_code is None")

if len(signal2_logs) != 1:
    errors.append(f"FAIL  Expected 1 enrich_record_soc_unclassified warning, got {len(signal2_logs)}")
else:
    print("  PASS  log.warning fired")


# ---------------------------------------------------------------------------
# SIGNAL 3: soc_unclassified_rate_exceeded + EnrichmentDegraded event
# ---------------------------------------------------------------------------

print("\n--- Signal 3: _check_soc_unclassified_rate at 100 % unclassified (5/5 records) ---")

_alert_bus = MagicMock()
register_alert_bus(_alert_bus)

with patch("enrichment.agent._alert_bus", _alert_bus), structlog.testing.capture_logs() as cap3:
    _check_soc_unclassified_rate(
        soc_classified_count=0,
        enriched_count=5,
        correlation_id="smoke-corr-001",
        batch_id="smoke-batch-001",
        triggered_by_event_type="SkillsExtracted",
    )

signal3_logs = [
    e for e in cap3 if e.get("log_level") == "warning" and e.get("event") == "soc_unclassified_rate_exceeded"
]

published = _alert_bus.publish.call_args_list
degraded_events = [c[0][0] for c in published if c[0][0].payload.get("classifier") == "soc"]

print(f"  soc_unclassified_rate_exceeded   : {len(signal3_logs)} warning(s)  (expected 1)")
print(f"  EnrichmentDegraded events        : {len(degraded_events)} event(s)   (expected 1)")

if len(signal3_logs) != 1:
    errors.append(f"FAIL  Expected 1 soc_unclassified_rate_exceeded warning, got {len(signal3_logs)}")
else:
    rw = signal3_logs[0]
    print(f"  PASS  log.warning fired  (rate={rw.get('unclassified_rate')}, threshold={rw.get('threshold')})")

if len(degraded_events) != 1:
    errors.append(f"FAIL  Expected 1 EnrichmentDegraded (soc) event, got {len(degraded_events)}")
else:
    p = degraded_events[0].payload
    print(
        f"  PASS  EnrichmentDegraded published  "
        f"(classifier={p['classifier']!r}, reason={p['reason']!r}, "
        f"unclassified_rate={p['unclassified_rate']})"
    )

# ---------------------------------------------------------------------------
# Final verdict
# ---------------------------------------------------------------------------

print()
if errors:
    for e in errors:
        print(e)
    print("\nSMOKE FAIL")
    sys.exit(1)
else:
    print("SMOKE PASS — all three signals verified")
