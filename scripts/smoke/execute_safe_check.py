#!/usr/bin/env python3
"""Week 8 smoke test — execute_safe round-trip check (Pairs B + C).

Calls :func:`analytics.query_engine.execute_safe.execute_validated_query` with
``SELECT 1 AS ping`` to verify that the query execution path, statement timeout
configuration, and result serialization all work end-to-end.

Usage (from any shell, any CWD):

    python scripts/smoke/execute_safe_check.py

Requires ``PYTHON_DATABASE_URL`` in the repo-root ``.env``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so analytics.* / common.* imports resolve
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.env import load_repo_root_dotenv  # noqa: E402

load_repo_root_dotenv()

from analytics.query_engine.execute_safe import execute_validated_query  # noqa: E402
from common.data_store.database import session_scope  # noqa: E402


def main() -> int:
    with session_scope() as s:
        rows, n = execute_validated_query(s, "SELECT 1 AS ping")
        print("rows:", rows, "count:", n)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
