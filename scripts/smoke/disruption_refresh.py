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
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print the first N fingerprint rows with disruption categories, ai_trend, wrs.",
    )
    parser.add_argument(
        "--show",
        type=int,
        default=3,
        help="Number of fingerprint rows to show in verbose mode (default 3).",
    )
    parser.add_argument(
        "--correlation-id",
        default="wk8-disruption-demo",
        help="Correlation id threaded through the event payload.",
    )
    args = parser.parse_args()

    svc = DisruptionFingerprintService()
    with session_scope() as session:
        result = svc.refresh_disruption_fingerprints(
            session=session,
            correlation_id=args.correlation_id,
        )

    print(f"roles_considered:    {result.roles_considered}")
    print(f"computed_count:      {result.computed_count}")
    if hasattr(result, "refresh_duration_ms"):
        print(f"refresh_duration_ms: {result.refresh_duration_ms}")

    if args.verbose and hasattr(result, "fingerprints") and result.fingerprints:
        print()
        for fp in result.fingerprints[: args.show]:
            cats = getattr(fp, "disruption_category", [])
            ai = getattr(fp, "ai_intensity_trend", "?")
            wrs = getattr(fp, "workflow_restructuring_score", 0.0)
            role_id = getattr(fp, "canonical_role_id", "?")
            print(f"  role={role_id}  cats={cats}  ai_trend={ai}  wrs={wrs:.3f}")

    if result.roles_considered == 0:
        print()
        print("NOTE: canonical_roles is empty. Run clustering first:")
        print("      python scripts/run_clustering.py")
        print("      (see runbook §0 Step 2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
