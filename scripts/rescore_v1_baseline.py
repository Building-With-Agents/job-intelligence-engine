# ruff: noqa: T201
"""Re-score v1-baseline traces with the v2 scorer for an apples-to-apples delta.

Fetches the 90 stored trace outputs from the v1-baseline Langfuse run, overlays
updated golden-question annotations (v2 fields: ``refusal_appropriate``,
``expected_min_rows``, ``zero_rows_is_correct``, ``expected_confidence_range``),
re-applies ``compute_item_scores`` with the new scorer logic, saves a comparison
JSON, and optionally uploads results to Langfuse as a new dataset run.

Usage (repo root, venv active)::

    # Fetch + rescore + save JSON (no upload)
    python scripts/rescore_v1_baseline.py

    # Fetch + rescore + create new Langfuse dataset run "v1-baseline-rescored"
    python scripts/rescore_v1_baseline.py --create-run v1-baseline-rescored

    # Override Langfuse host/keys (defaults read from .env)
    python scripts/rescore_v1_baseline.py \\
        --langfuse-host https://langfuse.watechcoalition.org/ \\
        --public-key pk-lf-... \\
        --secret-key sk-lf-...

Output: ``eval/runs/qa-v1-baseline-rescored.json``

Delta columns in the printed table::

    Δcomposite  = new composite  − old composite
    Δintent     = new intent_accuracy − old intent_accuracy
    Δevidence   = new evidence_citation − old evidence_citation
    Δconfidence = new confidence_self_consistency − old confidence_flags
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

from eval.qa_scoring import (  # noqa: E402
    QAItemScores,
    composite_score,
    compute_item_scores,
)

_V1_BASELINE_PATH = _REPO_ROOT / "eval" / "runs" / "qa-v1-baseline.json"
_GOLDEN_PATH = _REPO_ROOT / "eval" / "qa_golden_questions.json"
_OUTPUT_PATH = _REPO_ROOT / "eval" / "runs" / "qa-v1-baseline-rescored.json"
_DEFAULT_SLA = 30.0
_DEFAULT_DATASET_NAME = "LaborPulse Golden Questions"


# ---------------------------------------------------------------------------
# Langfuse API helpers
# ---------------------------------------------------------------------------


def _make_langfuse_api(host: str, public_key: str, secret_key: str):
    """Return a low-level LangfuseAPI client."""
    from langfuse.api import LangfuseAPI

    return LangfuseAPI(
        username=public_key,
        password=secret_key,
        base_url=host.rstrip("/"),
    )


def _make_langfuse_sdk(host: str, public_key: str, secret_key: str):
    """Return a high-level Langfuse SDK client (for dataset ops + scoring)."""
    from langfuse import Langfuse

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )


def _fetch_trace_output(api, trace_id: str) -> dict[str, Any] | None:
    """Fetch a trace by ID and return its ``output`` dict, or None on failure."""
    try:
        t = api.trace.get(trace_id)
        out = getattr(t, "output", None)
        if isinstance(out, dict):
            return out
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: fetch failed for trace {trace_id}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _rescore_item(
    output: dict[str, Any],
    updated_golden: dict[str, Any],
    sla_seconds: float,
) -> QAItemScores:
    """Apply v2 scorer to a fetched trace output with updated golden annotations."""
    return compute_item_scores(
        golden=updated_golden,
        response=output.get("response"),
        latency_seconds=float(output.get("latency_seconds") or 0.0),
        pipeline_error=output.get("pipeline_error"),
        sla_seconds=sla_seconds,
    )


def _old_composite(old_scores: dict[str, Any]) -> float | None:
    """Reconstruct v1 composite from stored scores (mean of four, skipping None)."""
    keys = ("intent_accuracy", "evidence_citation", "confidence_flags", "latency_sla")
    vals = [float(old_scores[k]) for k in keys if old_scores.get(k) is not None]
    return round(sum(vals) / len(vals), 6) if vals else None


# ---------------------------------------------------------------------------
# Langfuse dataset run creation
# ---------------------------------------------------------------------------


def _create_langfuse_run(
    lf,
    run_name: str,
    dataset_name: str,
    rescored_items: list[dict[str, Any]],
) -> str | None:
    """Create a new Langfuse dataset run with v2 scores.

    Looks up each dataset item by ``gq_id`` metadata, creates run items
    pointing to the original v1 traces, and annotates each with v2 scores.
    Returns the run URL on success or None on failure.
    """
    try:
        dataset = lf.get_dataset(dataset_name)
    except Exception as exc:
        print(f"  ERROR: could not fetch dataset {dataset_name!r}: {exc}")
        return None

    # Build gq_id → dataset item id map
    item_id_map: dict[str, str] = {}
    for di in dataset.items:
        meta = getattr(di, "metadata", None) or {}
        gq_id = str(meta.get("id") or getattr(di, "id", ""))
        if gq_id:
            item_id_map[gq_id] = str(di.id)

    if not item_id_map:
        print("  WARN: no dataset items found — skipping Langfuse run creation")
        return None

    print(f"  Found {len(item_id_map)} dataset items. Creating run {run_name!r}...")
    run_url: str | None = None
    n_ok = 0
    for item in rescored_items:
        gq_id: str = item["gq_id"]
        trace_id: str = item["trace_id"]
        dataset_item_id = item_id_map.get(gq_id)
        if not dataset_item_id:
            print(f"  WARN: no dataset item for {gq_id}, skipping")
            continue
        try:
            lf.create_dataset_run_item(
                run_name=run_name,
                dataset_item_id=dataset_item_id,
                trace_id=trace_id,
            )
            n_ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN: create_run_item failed for {gq_id}: {exc}")

    print(f"  Created {n_ok}/{len(rescored_items)} run items.")

    # Annotate with v2 scores
    n_scores = 0
    for item in rescored_items:
        trace_id = item["trace_id"]
        for score_name, val in item["v2_scores"].items():
            if val is not None:
                try:
                    lf.score(
                        trace_id=trace_id,
                        name=f"v2_{score_name}",
                        value=float(val),
                        comment=f"{run_name} v2-rescored",
                        data_type="NUMERIC",
                    )
                    n_scores += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  WARN: score upload failed {trace_id} {score_name}: {exc}")

    lf.flush()
    print(f"  Uploaded {n_scores} score annotations.")
    return run_url


# ---------------------------------------------------------------------------
# Console output helpers
# ---------------------------------------------------------------------------


def _fmt(v: float | None, width: int = 6) -> str:
    return f"{v:.4f}" if v is not None else " None "


def _delta(new: float | None, old: float | None) -> str:
    if new is None or old is None:
        return "   —  "
    d = new - old
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.4f}"


def _print_comparison_table(rows: list[dict[str, Any]]) -> None:
    header = (
        f"{'gq_id':>10}  "
        f"{'old_comp':>8}  {'new_comp':>8}  {'Δcomp':>7}  "
        f"{'Δintent':>7}  {'Δevidence':>9}  {'Δconfidence':>11}  "
        f"{'pipeline_err':>12}"
    )
    print("\n" + "=" * len(header))
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda x: x["delta_composite"] or 0):
        pe = (r.get("pipeline_error") or "")[:10] or "ok"
        print(
            f"{r['gq_id']:>10}  "
            f"{_fmt(r['old_composite']):>8}  {_fmt(r['new_composite']):>8}  "
            f"{_delta(r['new_composite'], r['old_composite']):>7}  "
            f"{_delta(r['v2_scores'].get('intent_accuracy'), r['v1_scores'].get('intent_accuracy')):>7}  "
            f"{_delta(r['v2_scores'].get('evidence_citation'), r['v1_scores'].get('evidence_citation')):>9}  "
            f"{_delta(r['v2_scores'].get('confidence_self_consistency'), r['v1_scores'].get('confidence_flags')):>11}  "
            f"{pe:>12}"
        )
    print("=" * len(header))


def _print_summary(rows: list[dict[str, Any]]) -> None:
    """Print mean old vs new composites and per-metric deltas."""
    old_comps = [r["old_composite"] for r in rows if r["old_composite"] is not None]
    new_comps = [r["new_composite"] for r in rows if r["new_composite"] is not None]
    old_mean = sum(old_comps) / len(old_comps) if old_comps else None
    new_mean = sum(new_comps) / len(new_comps) if new_comps else None

    print("\n=== v1-baseline RESCORE SUMMARY ===")
    print(f"  Items processed : {len(rows)}")
    print(f"  Old composite   : {_fmt(old_mean)}  (v1 scorer, v1 golden)")
    print(f"  New composite   : {_fmt(new_mean)}  (v2 scorer, v2 golden annotations)")
    if old_mean is not None and new_mean is not None:
        print(f"  Δ composite     : {_delta(new_mean, old_mean)}")

    # Per-metric means
    metrics = [
        ("intent_accuracy", "intent_accuracy", "intent_accuracy"),
        ("evidence_citation", "evidence_citation", "evidence_citation"),
        ("confidence_flags", "confidence_self_consistency", "confidence"),
        ("latency_sla", "latency_sla", "latency_sla"),
    ]
    print()
    print(f"  {'metric':>28}  {'old_mean':>8}  {'new_mean':>8}  {'Δ':>7}")
    print(f"  {'-' * 28}  {'-' * 8}  {'-' * 8}  {'-' * 7}")
    for old_key, new_key, label in metrics:
        old_vals = [r["v1_scores"].get(old_key) for r in rows if r["v1_scores"].get(old_key) is not None]
        new_vals = [r["v2_scores"].get(new_key) for r in rows if r["v2_scores"].get(new_key) is not None]
        o = sum(old_vals) / len(old_vals) if old_vals else None
        n = sum(new_vals) / len(new_vals) if new_vals else None
        print(f"  {label:>28}  {_fmt(o):>8}  {_fmt(n):>8}  {_delta(n, o):>7}")

    # answerability
    ab_old = [r["v1_scores"].get("answerability") for r in rows if r["v1_scores"].get("answerability") is not None]
    ab_new_vals = [r["v2_scores"].get("answerability") for r in rows if r["v2_scores"].get("answerability") is not None]
    o = sum(ab_old) / len(ab_old) if ab_old else None
    n = sum(ab_new_vals) / len(ab_new_vals) if ab_new_vals else None
    print(f"  {'answerability (data-backed)':>28}  {_fmt(o):>8}  {_fmt(n):>8}  {_delta(n, o):>7}")

    # correct_refusal (new in v2)
    cr_vals = [r["v2_scores"].get("correct_refusal") for r in rows if r["v2_scores"].get("correct_refusal") is not None]
    cr_mean = sum(cr_vals) / len(cr_vals) if cr_vals else None
    print(f"  {'correct_refusal (v2-new, n={len(cr_vals)})':>28}  {'N/A':>8}  {_fmt(cr_mean):>8}  {'  new':>7}")

    # infra exclusions
    n_pipe_err = sum(1 for r in rows if r.get("pipeline_error"))
    n_no_output = sum(1 for r in rows if r.get("output_missing"))
    print()
    print(f"  pipeline_error items   : {n_pipe_err}  (content metrics excluded, latency scored)")
    print(f"  missing trace output   : {n_no_output}  (could not fetch from Langfuse)")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Re-score v1-baseline traces with the v2 scorer for an apples-to-apples delta."
    )
    p.add_argument(
        "--v1-json",
        type=Path,
        default=_V1_BASELINE_PATH,
        help="Path to qa-v1-baseline.json (default: eval/runs/qa-v1-baseline.json)",
    )
    p.add_argument(
        "--golden-json",
        type=Path,
        default=_GOLDEN_PATH,
        help="Path to updated golden questions JSON (default: eval/qa_golden_questions.json)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=_OUTPUT_PATH,
        help="Where to write the rescored JSON (default: eval/runs/qa-v1-baseline-rescored.json)",
    )
    p.add_argument(
        "--langfuse-host",
        default=os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL", "http://localhost:3000"),
        help="Langfuse host URL",
    )
    p.add_argument(
        "--public-key",
        default=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        help="Langfuse public key",
    )
    p.add_argument(
        "--secret-key",
        default=os.getenv("LANGFUSE_SECRET_KEY", ""),
        help="Langfuse secret key",
    )
    p.add_argument(
        "--sla-seconds",
        type=float,
        default=_DEFAULT_SLA,
        help="Latency SLA threshold in seconds (default: 30.0)",
    )
    p.add_argument(
        "--create-run",
        default=None,
        metavar="RUN_NAME",
        help="Create a new Langfuse dataset run with this name and upload v2 scores",
    )
    p.add_argument(
        "--dataset-name",
        default=_DEFAULT_DATASET_NAME,
        help="Langfuse dataset name (for --create-run)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N items (for testing)",
    )
    p.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip Langfuse fetch; use cached output in rescored JSON if present",
    )
    args = p.parse_args(argv)

    # --- Load v1 baseline ---
    if not args.v1_json.exists():
        print(f"ERROR: v1-baseline JSON not found: {args.v1_json}")
        return 1
    v1_data = json.loads(args.v1_json.read_text())
    v1_items: list[dict[str, Any]] = v1_data.get("items", [])
    if args.limit:
        v1_items = v1_items[: args.limit]
    print(f"Loaded {len(v1_items)} v1-baseline items from {args.v1_json}")

    # --- Load updated golden ---
    if not args.golden_json.exists():
        print(f"ERROR: golden questions JSON not found: {args.golden_json}")
        return 1
    golden_list: list[dict[str, Any]] = json.loads(args.golden_json.read_text())
    golden_by_id: dict[str, dict[str, Any]] = {str(r["id"]): r for r in golden_list}
    print(f"Loaded {len(golden_by_id)} updated golden questions")

    # --- Connect to Langfuse ---
    host = args.langfuse_host.rstrip("/")
    pub = args.public_key
    sec = args.secret_key
    if not pub or not sec:
        print("ERROR: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set (or pass --public-key / --secret-key)")
        return 1

    print(f"\nConnecting to Langfuse: {host}")
    api = _make_langfuse_api(host, pub, sec)

    # --- Fetch + rescore ---
    rescored_items: list[dict[str, Any]] = []
    n_fetched = 0
    n_missing = 0
    t0 = time.time()

    for i, v1_item in enumerate(v1_items, 1):
        gq_id: str = str(v1_item.get("gq_id", ""))
        trace_id: str = str(v1_item.get("trace_id", ""))
        v1_scores: dict[str, Any] = v1_item.get("scores", {})
        updated_golden = golden_by_id.get(gq_id)

        if not updated_golden:
            print(f"  [{i:2d}] {gq_id}: WARN — no updated golden found, skipping")
            continue

        print(f"  [{i:2d}/{len(v1_items)}] {gq_id} — fetching trace {trace_id[:8]}...", end=" ", flush=True)
        output = _fetch_trace_output(api, trace_id)

        if output is None:
            n_missing += 1
            print("MISSING")
            rescored_items.append(
                {
                    "gq_id": gq_id,
                    "trace_id": trace_id,
                    "output_missing": True,
                    "pipeline_error": None,
                    "v1_scores": v1_scores,
                    "v2_scores": {},
                    "old_composite": _old_composite(v1_scores),
                    "new_composite": None,
                    "delta_composite": None,
                }
            )
            continue

        n_fetched += 1
        pipeline_error = output.get("pipeline_error")
        new_sc = _rescore_item(output, updated_golden, args.sla_seconds)
        new_comp = composite_score(new_sc)
        old_comp = _old_composite(v1_scores)
        delta = (new_comp - old_comp) if (new_comp is not None and old_comp is not None) else None
        status = f"Δ={_delta(new_comp, old_comp)}" + (" [pipe_err]" if pipeline_error else "")
        print(status)

        rescored_items.append(
            {
                "gq_id": gq_id,
                "trace_id": trace_id,
                "output_missing": False,
                "pipeline_error": pipeline_error,
                "v1_scores": v1_scores,
                "v2_scores": {
                    "intent_accuracy": new_sc.intent_accuracy,
                    "evidence_citation": new_sc.evidence_citation,
                    "confidence_self_consistency": new_sc.confidence_self_consistency,
                    "latency_sla": new_sc.latency_sla,
                    "answerability": new_sc.answerability,
                    "correct_refusal": new_sc.correct_refusal,
                    "confidence_in_expected_range": new_sc.confidence_in_expected_range,
                },
                "old_composite": old_comp,
                "new_composite": new_comp,
                "delta_composite": delta,
            }
        )

    elapsed = time.time() - t0
    print(f"\nFetched {n_fetched} traces, {n_missing} missing  ({elapsed:.1f}s)")

    # --- Print comparison ---
    _print_summary(rescored_items)
    _print_comparison_table(rescored_items)

    # --- Save output ---
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out_data: dict[str, Any] = {
        "source_run": str(args.v1_json),
        "golden_version": "v2-annotated",
        "scorer_version": "v2-redesign",
        "langfuse_host": host,
        "sla_seconds": args.sla_seconds,
        "n_items": len(rescored_items),
        "n_fetched": n_fetched,
        "n_missing": n_missing,
        "items": rescored_items,
    }
    # Compute run-level means for easy diff
    new_comps = [r["new_composite"] for r in rescored_items if r["new_composite"] is not None]
    old_comps = [r["old_composite"] for r in rescored_items if r["old_composite"] is not None]
    out_data["mean_old_composite"] = round(sum(old_comps) / len(old_comps), 6) if old_comps else None
    out_data["mean_new_composite"] = round(sum(new_comps) / len(new_comps), 6) if new_comps else None
    if out_data["mean_old_composite"] is not None and out_data["mean_new_composite"] is not None:
        out_data["delta_mean_composite"] = round(out_data["mean_new_composite"] - out_data["mean_old_composite"], 6)

    args.output.write_text(json.dumps(out_data, indent=2))
    print(f"Saved rescored results → {args.output}")

    # --- Optionally create Langfuse run ---
    if args.create_run:
        print(f"\nCreating Langfuse dataset run: {args.create_run!r}")
        lf = _make_langfuse_sdk(host, pub, sec)
        _create_langfuse_run(lf, args.create_run, args.dataset_name, rescored_items)

    return 0


if __name__ == "__main__":
    sys.exit(main())
