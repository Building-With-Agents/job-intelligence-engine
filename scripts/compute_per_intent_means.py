"""Aggregate Q&A eval item scores by golden intent (JIE #256).

Reads a JSON document with an ``items`` array in the shape emitted by
``python -m eval.qa_eval --dry-run --json`` (each item has ``gq_id``,
``scores``, and optionally ``error``).

Usage (repo root)::

    python -m eval.qa_eval --prompt-version t --dry-run --json \\
      | python scripts/compute_per_intent_means.py

Or from a file::

    python scripts/compute_per_intent_means.py path/to/summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _load_payload(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("root must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "json_file",
        nargs="?",
        default=None,
        help="Path to eval summary JSON (default: read stdin)",
    )
    args = p.parse_args(argv)

    raw = Path(args.json_file).read_text(encoding="utf-8") if args.json_file else sys.stdin.read()

    payload = _load_payload(raw)
    items = payload.get("items")
    if not isinstance(items, list):
        print("No items array in JSON; nothing to aggregate.", file=sys.stderr)
        return 1

    golden_path = Path(__file__).resolve().parents[1] / "eval" / "qa_golden_questions.json"
    intent_by_id: dict[str, str] = {}
    if golden_path.exists():
        gq = json.loads(golden_path.read_text(encoding="utf-8"))
        if isinstance(gq, list):
            for row in gq:
                if isinstance(row, dict) and row.get("id") is not None:
                    intent_by_id[str(row["id"])] = str(row.get("intent") or "")

    metric_keys = (
        "intent_accuracy",
        "evidence_citation",
        "must_include_recall",
        "evidence_overlap",
        "confidence_self_consistency",
        "latency_sla",
        "answerability",
        "correct_refusal",
    )

    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    n_items: dict[str, int] = defaultdict(int)

    for it in items:
        if not isinstance(it, Mapping):
            continue
        gq_id = str(it.get("gq_id") or "")
        intent = intent_by_id.get(gq_id, "?")
        scores = it.get("scores")
        if not isinstance(scores, Mapping):
            continue
        n_items[intent] += 1
        for mk in metric_keys:
            v = scores.get(mk)
            if isinstance(v, (int, float)):
                buckets[intent][mk].append(float(v))

    out: dict[str, Any] = {}
    for intent in sorted(buckets):
        per_m: dict[str, float | None] = {}
        for mk in metric_keys:
            xs = buckets[intent][mk]
            per_m[mk] = sum(xs) / len(xs) if xs else None
        out[intent] = {"n_items": n_items[intent], "means": per_m}

    sys.stdout.write(json.dumps({"per_intent": out}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
