"""Compare Layer 1 automated scores against Layer 2 human scores (JIE #271 Change 3).

Reads automated eval scores from a ``qa_eval --json`` run summary and human
aggregate scores from Langfuse (``correctness_aggregate`` scores emitted by
``compute_layer2_irr.py``), then produces a sorted divergence report that
identifies items where the two scoring layers disagree most.

The top of the divergence list is the Week 10 iteration shortlist:
- High automated, low human  → likely confident hallucination; Layer 1 scored structure
- Low automated, high human  → likely semantic paraphrase; Layer 1 missed a lexical match

Usage (repo root, venv active)::

    # Step 1: run the eval harness and save output
    python -m eval.qa_eval --prompt-version v1-baseline --dry-run --json \\
        --output-json eval/runs/v1-baseline-scores.json

    # Step 2: run IRR to emit aggregate scores to Langfuse
    python scripts/compute_layer2_irr.py --run-name v1-baseline

    # Step 3: produce divergence report
    python scripts/compute_automated_human_divergence.py \\
        --automated-json eval/runs/v1-baseline-scores.json \\
        --dataset-name "LaborPulse Golden Questions" \\
        --run-name v1-baseline

    # Without Langfuse (human scores as JSON):
    python scripts/compute_automated_human_divergence.py \\
        --automated-json eval/runs/v1-baseline-scores.json \\
        --human-json eval/runs/v1-baseline-irr.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

import structlog  # noqa: E402

from eval.qa_scoring import score_confidence_correctness_alignment  # noqa: E402

log = structlog.get_logger()

_DEFAULT_DATASET = "LaborPulse Golden Questions"
_DEFAULT_OUTPUT = _REPO_ROOT / "eval" / "qa_divergence_report.md"


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_automated_scores(path: Path) -> dict[str, dict[str, Any]]:
    """Load per-item automated scores from a ``qa_eval --json`` output.

    Returns: gq_id -> {metric: value, ...}
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for it in raw.get("items", []):
        gq_id = str(it.get("gq_id") or "")
        scores = it.get("scores") or {}
        if gq_id:
            result[gq_id] = {
                "evidence_citation": scores.get("evidence_citation"),
                "intent_accuracy": scores.get("intent_accuracy"),
                "confidence_self_consistency": scores.get("confidence_self_consistency"),
                "latency_sla": scores.get("latency_sla"),
                "composite": scores.get("composite"),
                "error": it.get("error"),
                "trace_id": str(it.get("trace_id") or ""),
            }
    return result


def _load_human_aggregates_from_langfuse(
    lf: Any, dataset_name: str, run_name: str
) -> dict[str, dict[str, float | None]]:
    """Fetch ``*_aggregate`` scores from Langfuse for each trace in the run.

    Returns: trace_id -> {correctness_aggregate, decision_relevance_aggregate, followup_quality_aggregate}
    """
    run = lf.get_dataset_run(dataset_name=dataset_name, dataset_run_name=run_name)
    result: dict[str, dict[str, float | None]] = {}
    for item in run.dataset_run_items:
        trace_id = str(item.trace_id or "")
        if not trace_id:
            continue
        agg_scores: dict[str, float | None] = {
            "correctness_aggregate": None,
            "decision_relevance_aggregate": None,
            "followup_quality_aggregate": None,
        }
        # Fetch all aggregate score types
        for score_name in list(agg_scores.keys()):
            resp_n = lf.fetch_scores(trace_id=trace_id, name=score_name)
            data = resp_n.data if hasattr(resp_n, "data") else resp_n
            if data:
                latest = data[0]
                val = getattr(latest, "value", None)
                if isinstance(val, (int, float)):
                    agg_scores[score_name] = float(val)
        result[trace_id] = agg_scores
    return result


def _load_human_aggregates_from_json(path: Path) -> dict[str, dict[str, float | None]]:
    """Load human aggregate scores from a compute_layer2_irr.py --json-output file.

    Returns: trace_id -> {correctness_aggregate, ...}
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, float | None]] = {}
    for trace_id, agg in raw.get("aggregates", {}).items():
        result[str(trace_id)] = {
            "correctness_aggregate": agg.get("correctness_aggregate"),
            "decision_relevance_aggregate": agg.get("decision_relevance_aggregate"),
            "followup_quality_aggregate": agg.get("followup_quality_aggregate"),
        }
    return result


# ---------------------------------------------------------------------------
# Divergence computation
# ---------------------------------------------------------------------------


def _compute_divergence_rows(
    automated: dict[str, dict[str, Any]],
    human_by_trace: dict[str, dict[str, float | None]],
    automated_trace_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Compute divergence for items that have both automated and human scores.

    ``automated_trace_map``: gq_id -> trace_id (populated from automated scores JSON).

    Returns list of item dicts sorted by |automated_evidence_citation - correctness_aggregate| desc.
    """
    rows: list[dict[str, Any]] = []
    for gq_id, auto in automated.items():
        trace_id = automated_trace_map.get(gq_id, auto.get("trace_id", ""))
        human = human_by_trace.get(str(trace_id)) if trace_id else None
        if human is None:
            continue

        ev_auto = auto.get("evidence_citation")
        corr_human = human.get("correctness_aggregate")

        if ev_auto is None or corr_human is None:
            continue

        delta = abs(float(ev_auto) - float(corr_human))

        # Alignment score (1 - distance between confidence and correctness)
        conf_self = auto.get("confidence_self_consistency") or 0.0
        align, align_comment = score_confidence_correctness_alignment(
            confidence=float(conf_self),
            correctness=float(corr_human),
        )

        direction = "high-auto-low-human" if float(ev_auto) > float(corr_human) else "high-human-low-auto"

        rows.append(
            {
                "gq_id": gq_id,
                "trace_id": trace_id,
                "evidence_citation_auto": round(float(ev_auto), 4),
                "correctness_human": round(float(corr_human), 4),
                "delta": round(delta, 4),
                "direction": direction,
                "decision_relevance_aggregate": human.get("decision_relevance_aggregate"),
                "followup_quality_aggregate": human.get("followup_quality_aggregate"),
                "confidence_self_consistency": auto.get("confidence_self_consistency"),
                "alignment_score": round(float(align), 4) if align is not None else None,
                "alignment_comment": align_comment,
                "intent_accuracy": auto.get("intent_accuracy"),
                "latency_sla": auto.get("latency_sla"),
                "composite": auto.get("composite"),
                "error": auto.get("error"),
            }
        )

    rows.sort(key=lambda r: r["delta"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _render_divergence_report(
    run_name: str,
    rows: list[dict[str, Any]],
    top_n: int,
) -> str:
    lines = [
        f"# Layer 1 vs Layer 2 Divergence Report — run: `{run_name}`",
        "",
        "Generated by `scripts/compute_automated_human_divergence.py`.",
        "",
        "## Interpretation",
        "",
        "- **high-auto-low-human**: automated `evidence_citation` high but human `correctness` low.",
        "  Likely confident hallucination — Layer 1 scored structure, Layer 2 scored truth.",
        "  → Priority prompt iteration target.",
        "- **high-human-low-auto**: automated score low but human score high.",
        "  Likely semantic paraphrase — Layer 1 missed a lexical match, Layer 2 understood.",
        "  → Rubric loosening or evidence scoring redesign candidate.",
        "",
        f"## Top {min(top_n, len(rows))} items by |evidence_citation − correctness_aggregate|",
        "",
        f"Total items with both scores: {len(rows)}",
        "",
        "| # | gq_id | auto_ec | human_corr | delta | direction |",
        "|---|-------|---------|-----------|-------|-----------|",
    ]
    for i, row in enumerate(rows[:top_n], 1):
        lines.append(
            f"| {i} | {row['gq_id']} "
            f"| {row['evidence_citation_auto']:.3f} "
            f"| {row['correctness_human']:.3f} "
            f"| {row['delta']:.3f} "
            f"| {row['direction']} |"
        )
    lines += [""]

    if rows:
        lines += [
            "## Item details",
            "",
        ]
        for i, row in enumerate(rows[:top_n], 1):
            lines += [
                f"### {i}. {row['gq_id']}",
                "",
                f"- automated `evidence_citation`: {row['evidence_citation_auto']}",
                f"- human `correctness_aggregate`: {row['correctness_human']}",
                f"- delta: **{row['delta']:.4f}** ({row['direction']})",
                f"- confidence_self_consistency: {row.get('confidence_self_consistency')}",
                f"- alignment_score: {row.get('alignment_score')} ({row.get('alignment_comment', '')})",
                f"- intent_accuracy: {row.get('intent_accuracy')}",
                f"- composite: {row.get('composite')}",
                f"- decision_relevance_aggregate: {row.get('decision_relevance_aggregate')}",
                f"- followup_quality_aggregate: {row.get('followup_quality_aggregate')}",
                f"- trace_id: `{row.get('trace_id', '')}`",
                "",
            ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--automated-json",
        type=Path,
        required=True,
        help="Path to qa_eval --json output (per-item automated scores)",
    )
    p.add_argument("--dataset-name", default=_DEFAULT_DATASET, help="Langfuse dataset name")
    p.add_argument(
        "--run-name",
        default=None,
        help="Langfuse dataset run name (for fetching human aggregates from Langfuse)",
    )
    p.add_argument(
        "--human-json",
        type=Path,
        default=None,
        help="Path to compute_layer2_irr.py --json-output (alternative to Langfuse fetch)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help=f"Markdown report path (default: {_DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Also write divergence rows as JSON to this path",
    )
    p.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Number of top divergent items to show in the report (default: 20)",
    )
    args = p.parse_args(argv)

    if not args.automated_json.exists():
        print(f"Automated scores file not found: {args.automated_json}", file=sys.stderr)
        return 1

    automated = _load_automated_scores(args.automated_json)
    if not automated:
        print("No items found in automated scores JSON.", file=sys.stderr)
        return 1

    automated_trace_map: dict[str, str] = {gq_id: scores.get("trace_id", "") for gq_id, scores in automated.items()}

    # Load human aggregates
    human_by_trace: dict[str, dict[str, float | None]] = {}
    if args.human_json and args.human_json.exists():
        human_by_trace = _load_human_aggregates_from_json(args.human_json)
        log.info("loaded_human_aggregates_from_json", path=str(args.human_json), n=len(human_by_trace))
    elif args.run_name:
        from langfuse import Langfuse

        lf = Langfuse()
        try:
            human_by_trace = _load_human_aggregates_from_langfuse(lf, args.dataset_name, args.run_name)
            log.info("loaded_human_aggregates_from_langfuse", n=len(human_by_trace))
        except Exception as exc:  # noqa: BLE001
            log.error("langfuse_fetch_failed", error=str(exc))
            lf.shutdown()
            return 1
        lf.flush()
        lf.shutdown()
    else:
        print("Provide --human-json or --run-name to load human aggregate scores.", file=sys.stderr)
        return 1

    if not human_by_trace:
        print(
            "No human aggregate scores found. Run compute_layer2_irr.py first to emit aggregates.",
            file=sys.stderr,
        )
        return 0

    rows = _compute_divergence_rows(automated, human_by_trace, automated_trace_map)

    if not rows:
        print(
            "No items have both automated and human scores. Check that trace_ids align.",
            file=sys.stderr,
        )
        return 0

    run_label = args.run_name or str(args.automated_json.stem)
    report = _render_divergence_report(run_label, rows, args.top_n)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote {args.output}")

    if args.json_output:
        args.json_output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Wrote {args.json_output}")

    # Console summary
    print(f"\nItems with both scores: {len(rows)}")
    print(f"Top divergent (delta > 0.3): {sum(1 for r in rows if r['delta'] > 0.3)}")
    for row in rows[: min(5, len(rows))]:
        print(
            f"  {row['gq_id']}: auto_ec={row['evidence_citation_auto']:.3f} "
            f"human_corr={row['correctness_human']:.3f} delta={row['delta']:.3f} "
            f"[{row['direction']}]"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
