#!/usr/bin/env python3
"""Week 8 smoke test — verify Streamlit dashboard page imports (Pairs C + D).

Imports all 4 new Week 8 dashboard page modules to confirm no import errors
in the dependency chain.  Does NOT launch a Streamlit server — that is a
manual step (``streamlit run dashboard/app.py``).

Use this as a fast pre-flight check before opening the browser.

Usage (from any shell, any CWD):

    python scripts/smoke/streamlit_check.py
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Ensure repo root is on sys.path so dashboard.* / common.* imports resolve
# when the script is invoked from any CWD.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

# Pages to validate — module paths relative to repo root
_PAGES = [
    ("Ask the Data (Pair C)", "dashboard.pages_ask_the_data"),
    ("Skills Gap Map (Pair D)", "dashboard.pages_skills_gap_map"),
    ("Emergence Alerts (Pair C)", "dashboard.pages_emergence_alerts"),
    ("Regional Heatmap (Pair D)", "dashboard.pages_regional_heatmap"),
]


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    print("Importing Week 8 Streamlit page modules...\n")

    for label, module_path in _PAGES:
        try:
            importlib.import_module(module_path)
            results.append((label, True, ""))
            print(f"  OK   {label}  ({module_path})")
        except Exception as exc:
            results.append((label, False, str(exc)))
            print(f"  FAIL {label}  ({module_path})")
            print(f"       {type(exc).__name__}: {exc}")

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print("STREAMLIT IMPORT CHECK SUMMARY")
    print(f"{'=' * 60}")
    total_ok = sum(1 for _, ok, _ in results if ok)
<<<<<<< HEAD
    for label, ok, _ in results:
=======
    for label, ok, _err in results:
>>>>>>> origin
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {label}")
    print(f"\n{total_ok}/{len(results)} pages imported successfully")

    if total_ok < len(results):
        print(
            "\nTIP: Import failures usually mean a missing dependency.\n"
            "     Run: pip install -r requirements.txt\n"
            "     Then retry this script."
        )

    return 0 if total_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
