#!/usr/bin/env python3
"""Week 8 smoke test — refresh disruption fingerprints once (Pair A).

Runs :class:`analytics.disruption.service.DisruptionFingerprintService.refresh_disruption_fingerprints`
against the configured database and prints the summary result. Useful as a
demo-day sanity check and as the first-ever populate of
``dbo.disruption_fingerprints`` on a fresh dev database.

Usage (from any shell, any CWD):

    python scripts/smoke/disruption_refresh.py

Requires ``PYTHON_DATABASE_URL`` in the repo-root ``.env`` and populated
``dbo.canonical_roles``. If ``canonical_roles`` is empty (common on a fresh
local dev DB), the script still completes cleanly and reports
``roles_considered=0, computed_count=0``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so analytics.* / common.* imports resolve
# when the script is invoked from any CWD.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.disruption.service import DisruptionFingerprintService  # noqa: E402
from common.data_store.database import session_scope  # noqa: E402


def main() -> int:
    svc = DisruptionFingerprintService()
    with session_scope() as session:
        result = svc.refresh_disruption_fingerprints(session=session)

    print(f"roles_considered:    {result.roles_considered}")
    print(f"computed_count:      {result.computed_count}")
    if hasattr(result, "refresh_duration_ms"):
        print(f"refresh_duration_ms: {result.refresh_duration_ms}")

    if result.roles_considered == 0:
        print()
        print("NOTE: canonical_roles is empty. Run Pair C's clustering flow first")
        print("      (WEEK07_TESTING_RUNBOOK.md Section 5) to populate canonical_roles,")
        print("      then re-run this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
