"""Register Azure OpenAI deployment models in Langfuse for cost attribution (JIE #259).

One-liner: Langfuse UI cost is $0 when models are not in the project registry or
observations omit ``usage_details``. This script registers deployment + API alias
names with per-token pricing from ``config/llm_costs.yaml`` (via ``PRICING``).

Run once per Langfuse project. Idempotent: skips models whose ``modelName`` already
exists. Safe to re-run after a new Langfuse instance is provisioned.

Usage::

    python scripts/setup_langfuse_models.py
    python scripts/setup_langfuse_models.py --dry-run

Exit code 0 = success. Non-zero = create call failed; check ``LANGFUSE_*`` in ``.env``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

from langfuse import Langfuse  # noqa: E402
from langfuse.api.commons.types.model_usage_unit import ModelUsageUnit  # noqa: E402

from common.llm_adapter import PRICING  # noqa: E402

# (Langfuse model_name, PRICING tier key in llm_adapter.PRICING)
_MODELS: tuple[tuple[str, str], ...] = (
    ("chat-gpt41mini", "gpt-4.1-mini"),
    ("chat-gpt41", "gpt-4.1"),
    ("gpt-4.1-mini-2025-04-14", "gpt-4.1-mini"),
    ("gpt-4.1-2025-04-14", "gpt-4.1"),
)


def _match_pattern(model_name: str) -> str:
    """Exact case-insensitive match for generation.model."""
    return f"(?i)^{model_name}$"


def _existing(lf: Langfuse) -> dict[str, str]:
    """Return {model_name: id} of currently registered models, paginating."""
    out: dict[str, str] = {}
    page = 1
    while True:
        resp = lf.api.models.list(page=page, limit=100)
        for m in resp.data:
            out[m.model_name] = m.id
        meta = getattr(resp, "meta", None)
        total_pages = getattr(meta, "total_pages", 1) if meta else 1
        if page >= total_pages:
            break
        page += 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created; do not mutate Langfuse state.",
    )
    args = parser.parse_args()

    lf = Langfuse()

    import os

    host = os.environ.get("LANGFUSE_BASE_URL") or "<unset>"
    print(f"Host: {host}")
    print()

    print("== Current Models ==")
    existing = _existing(lf)
    for name, mid in sorted(existing.items()):
        print(f"  [ok]    {name:30s}  id={mid}")
    if not existing:
        print("  (none)")
    print()

    to_create: list[tuple[str, str, float, float]] = []
    skipped: list[str] = []
    for model_name, tier_key in _MODELS:
        if model_name in existing:
            skipped.append(model_name)
            continue
        tier = PRICING.get(tier_key)
        if not tier:
            print(f"  [error]  {model_name:30s}  missing PRICING key {tier_key!r}")
            return 1
        to_create.append((model_name, tier_key, tier["input"], tier["output"]))

    print(f"== Plan: create {len(to_create)}, skip {len(skipped)} ==")
    for model_name, tier_key, inp, out in to_create:
        print(
            f"  [create] {model_name:30s}  tier={tier_key:12s}  "
            f"input=${inp:.2e}/tok  output=${out:.2e}/tok"
        )
    for name in skipped:
        print(f"  [skip]   {name:30s}  (already registered)")
    print()

    if args.dry_run:
        print("Dry-run: no changes applied.")
        return 0

    if not to_create:
        print("Nothing to do — all models in _MODELS are already registered.")
        return 0

    print("== Creating ==")
    for model_name, tier_key, input_price, output_price in to_create:
        result = lf.api.models.create(
            model_name=model_name,
            match_pattern=_match_pattern(model_name),
            unit=ModelUsageUnit.TOKENS,
            input_price=input_price,
            output_price=output_price,
        )
        print(
            f"  [created] {result.model_name:30s}  id={result.id}  "
            f"tier={tier_key}"
        )

    print()
    print(f"Done. {len(to_create)} model(s) created; {len(skipped)} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
