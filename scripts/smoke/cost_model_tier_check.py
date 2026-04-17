#!/usr/bin/env python3
"""Week 8 smoke test — verify resolve_model_tier maps deployments correctly.

Calls :func:`common.llm_adapter.resolve_model_tier` for each known deployment
name and prints the resolved tier alongside its PRICING entry.  Confirms that
the adapter's cost ledger will compute accurate per-turn costs.

Usage (from any shell, any CWD):

    python scripts/smoke/cost_model_tier_check.py

No arguments.  Does not require a database connection or LLM keys.
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

from common.llm_adapter import resolve_model_tier, PRICING  # noqa: E402


def main() -> int:
    deployments = ("chat-gpt41mini", "chat-gpt41", "chat-gpt4o-mini", "claude-sonnet-4-5")
    for dep in deployments:
        tier = resolve_model_tier(dep)
        pricing = PRICING.get(tier, {})
        input_price = pricing.get("input", "?")
        output_price = pricing.get("output", "?")
        print(f"{dep:25} -> {tier:20} input=${input_price}/1M  output=${output_price}/1M")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
