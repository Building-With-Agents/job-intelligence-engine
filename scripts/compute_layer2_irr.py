"""Compute Layer 2 inter-rater reliability (IRR) from Langfuse human scores.

Reads human scores (correctness, decision_relevance, followup_quality) for a
named dataset run, computes per-metric IRR statistics across scorer pairs, and
produces:

1. eval/qa_irr_report.md — kappa, Pearson r, disagreement distribution per metric
2. Langfuse aggregate scores  — correctness_aggregate, decision_relevance_aggregate,
   followup_quality_aggregate per trace (skipped when --dry-run)
3. Run-level Langfuse scores  — mean_human_correctness_composite, mean_confidence_ece
   (skipped when --dry-run)

Usage (repo root, venv active)::

    python scripts/compute_layer2_irr.py \\
        --dataset-name "LaborPulse Golden Questions" \\
        --run-name v1-baseline

    python scripts/compute_layer2_irr.py --run-name v1-baseline --dry-run

    python scripts/compute_layer2_irr.py --run-name v1-baseline \\
        --output eval/qa_irr_report.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

import structlog  # noqa: E402

from eval.qa_scoring import (  # noqa: E402
    LAYER2_METRIC_NAMES,
    aggregate_human_scores,
    human_correctness_composite,
    run_ece_from_correctness,
)

log = structlog.get_logger()

_DEFAULT_DATASET = "LaborPulse Golden Questions"
_DEFAULT_OUTPUT = _REPO_ROOT / "eval" / "qa_irr_report.md"

# Discretize continuous [0,1] scores into low/medium/high for kappa.
_KAPPA_BINS = [(0.0, 0.4, "low"), (0.4, 0.7, "medium"), (0.7, 1.01, "high")]


def _discretize(v: float) -> str:
    for lo, hi, label in _KAPPA_BINS:
        if lo <= v < hi:
            return label
    return "high"


# ---------------------------------------------------------------------------
# Statistical helpers — no third-party deps; standard library only.
# ---------------------------------------------------------------------------


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r between two equal-length numeric lists. Returns None when undefined."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    denom_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denom_x < 1e-9 or denom_y < 1e-9:
        return None
    return num / (denom_x * denom_y)


def _cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float | None:
    """Cohen's kappa for two equal-length label sequences."""
    n = len(labels_a)
    if n < 2 or len(labels_b) != n:
        return None
    cats = sorted({*labels_a, *labels_b})
    # Observed agreement
    p_obs = sum(1 for a, b in zip(labels_a, labels_b, strict=False) if a == b) / n
    # Expected agreement
    p_exp = 0.0
    for c in cats:
        pa = labels_a.count(c) / n
        pb = labels_b.count(c) / n
        p_exp += pa * pb
    if abs(1.0 - p_exp) < 1e-9:
        return 1.0 if abs(p_obs - 1.0) < 1e-9 else 0.0
    return (p_obs - p_exp) / (1.0 - p_exp)


def _disagreement_distribution(scores_a: list[float], scores_b: list[float]) -> dict[str, int]:
    """Histogram of |a - b| in 0.1-wide bins."""
    bins: dict[str, int] = {f"{lo:.1f}-{lo + 0.1:.1f}": 0 for lo in [i / 10 for i in range(10)]}
    bins["1.0"] = 0
    for a, b in zip(scores_a, scores_b, strict=False):
        delta = abs(a - b)
        key = f"{min(int(delta * 10) / 10, 0.9):.1f}-{min(int(delta * 10) / 10 + 0.1, 1.0):.1f}"
        if delta >= 1.0:
            key = "1.0"
        bins[key] = bins.get(key, 0) + 1
    return bins


# ---------------------------------------------------------------------------
# Langfuse data retrieval
# ---------------------------------------------------------------------------


def _fetch_run_scores(
    lf: Any,
    dataset_name: str,
    run_name: str,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch all scores for every trace in the named dataset run.

    Returns: trace_id -> list of score dicts (name, value, comment, author_user_id, source).
    """
    run = lf.get_dataset_run(dataset_name=dataset_name, dataset_run_name=run_name)
    trace_scores: dict[str, list[dict[str, Any]]] = {}
    for item in run.dataset_run_items:
        trace_id = str(item.trace_id or "")
        if not trace_id:
            continue
        resp = lf.fetch_scores(trace_id=trace_id)
        trace_scores[trace_id] = [
            {
                "name": getattr(s, "name", None),
                "value": getattr(s, "value", None),
                "comment": getattr(s, "comment", None) or "",
                "author_user_id": getattr(s, "author_user_id", None),
                "source": str(getattr(s, "source", "API") or "API").upper(),
            }
            for s in (resp.data if hasattr(resp, "data") else resp)
        ]
    return trace_scores


def _filter_human_scores(
    trace_scores: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, dict[str, float]]]:
    """Extract human Layer 2 scores.

    Returns: trace_id -> metric_name -> annotator_id -> score_value.

    When author_user_id is unavailable, annotators are distinguished by
    source=HUMAN|REVIEW or by arrival order within the metric.
    """
    result: dict[str, dict[str, dict[str, float]]] = {}
    for trace_id, scores in trace_scores.items():
        per_metric: dict[str, dict[str, float]] = defaultdict(dict)
        anon_counter: dict[str, int] = defaultdict(int)
        for s in scores:
            name = str(s.get("name") or "")
            if name not in LAYER2_METRIC_NAMES:
                continue
            val_raw = s.get("value")
            try:
                val = max(0.0, min(1.0, float(val_raw)))
            except (TypeError, ValueError):
                continue
            author = s.get("author_user_id")
            if not author:
                anon_counter[name] += 1
                author = f"annotator_{anon_counter[name]}"
            per_metric[name][author] = val
        if per_metric:
            result[trace_id] = dict(per_metric)
    return result


# ---------------------------------------------------------------------------
# IRR computation
# ---------------------------------------------------------------------------


def _compute_metric_irr(
    human_scores: dict[str, dict[str, dict[str, float]]],
    metric: str,
) -> dict[str, Any]:
    """Per-metric IRR across all scorer pairs that share at least 2 common traces."""
    # Collect (annotator -> list[(trace_id, score)]) in trace order
    annotator_traces: dict[str, dict[str, float]] = defaultdict(dict)
    for trace_id, metrics in human_scores.items():
        for annotator, score in metrics.get(metric, {}).items():
            annotator_traces[annotator][trace_id] = score

    annotators = sorted(annotator_traces.keys())
    if len(annotators) < 2:
        return {
            "annotators": annotators,
            "n_common_traces": 0,
            "cohens_kappa": None,
            "pearson_r": None,
            "disagreement_distribution": {},
            "mean_absolute_disagreement": None,
            "note": "fewer than 2 annotators — IRR not computable",
        }

    # Pairwise stats across all pairs (typical case: 2 annotators)
    pair_results: list[dict[str, Any]] = []
    for i in range(len(annotators)):
        for j in range(i + 1, len(annotators)):
            a1, a2 = annotators[i], annotators[j]
            common = sorted(set(annotator_traces[a1]) & set(annotator_traces[a2]))
            if len(common) < 2:
                pair_results.append(
                    {
                        "pair": [a1, a2],
                        "n_common": len(common),
                        "cohens_kappa": None,
                        "pearson_r": None,
                        "disagreement_distribution": {},
                        "mean_absolute_disagreement": None,
                    }
                )
                continue
            scores_1 = [annotator_traces[a1][t] for t in common]
            scores_2 = [annotator_traces[a2][t] for t in common]
            labels_1 = [_discretize(s) for s in scores_1]
            labels_2 = [_discretize(s) for s in scores_2]
            kappa = _cohen_kappa(labels_1, labels_2)
            r = _pearson(scores_1, scores_2)
            dist = _disagreement_distribution(scores_1, scores_2)
            mad = sum(abs(a - b) for a, b in zip(scores_1, scores_2, strict=False)) / len(common)
            pair_results.append(
                {
                    "pair": [a1, a2],
                    "n_common": len(common),
                    "cohens_kappa": round(kappa, 4) if kappa is not None else None,
                    "pearson_r": round(r, 4) if r is not None else None,
                    "disagreement_distribution": dist,
                    "mean_absolute_disagreement": round(mad, 4),
                }
            )

    # Flatten single-pair case for report readability
    if len(pair_results) == 1:
        pr = pair_results[0]
        return {
            "annotators": annotators,
            "n_common_traces": pr["n_common"],
            "cohens_kappa": pr["cohens_kappa"],
            "pearson_r": pr["pearson_r"],
            "disagreement_distribution": pr["disagreement_distribution"],
            "mean_absolute_disagreement": pr["mean_absolute_disagreement"],
        }

    return {"annotators": annotators, "pairs": pair_results}


# ---------------------------------------------------------------------------
# Aggregate score emission
# ---------------------------------------------------------------------------


def _compute_aggregates(
    human_scores: dict[str, dict[str, dict[str, float]]],
) -> dict[str, dict[str, float | None]]:
    """Compute aggregate scores per trace per metric."""
    result: dict[str, dict[str, float | None]] = {}
    for trace_id, metrics in human_scores.items():
        agg: dict[str, float | None] = {}
        for metric in LAYER2_METRIC_NAMES:
            values = list(metrics.get(metric, {}).values())
            agg[f"{metric}_aggregate"] = aggregate_human_scores(values)
        result[trace_id] = agg
    return result


def _emit_scores(lf: Any, trace_id: str, scores: dict[str, float | None]) -> None:
    """Emit aggregate scores back to Langfuse for a single trace."""
    for name, value in scores.items():
        if value is None:
            continue
        try:
            lf.score(
                trace_id=trace_id,
                name=name,
                value=float(value),
                comment="layer2 aggregate (compute_layer2_irr.py)",
                data_type="NUMERIC",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("langfuse_score_emit_failed", trace_id=trace_id, name=name, error=str(exc))


# ---------------------------------------------------------------------------
# ECE from run
# ---------------------------------------------------------------------------


def _compute_run_ece(
    human_scores: dict[str, dict[str, dict[str, float]]],
    trace_automated: dict[str, float],
) -> float | None:
    """ECE using automated confidence scores and aggregated human correctness."""
    confidences: list[float] = []
    correctness: list[bool | None] = []
    for trace_id, aggs in human_scores.items():
        conf = trace_automated.get(trace_id)
        corr_values = list(aggs.get("correctness", {}).values())
        corr_agg = aggregate_human_scores(corr_values)
        if conf is not None and corr_agg is not None:
            confidences.append(float(conf))
            correctness.append(corr_agg)
    return run_ece_from_correctness(confidences, correctness)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _render_report(
    run_name: str,
    irr_by_metric: dict[str, dict[str, Any]],
    aggregates: dict[str, dict[str, float | None]],
    ece: float | None,
    hcc: float | None,
) -> str:
    lines: list[str] = [
        f"# Layer 2 IRR Report — run: `{run_name}`",
        "",
        "Generated by `scripts/compute_layer2_irr.py`.",
        "",
        "## Summary",
        "",
        f"- Items with at least one Layer 2 score: **{len(aggregates)}**",
        f"- Mean human correctness composite: **{hcc:.4f}**"
        if hcc is not None
        else "- Mean human correctness composite: n/a",
        f"- ECE (lower is better): **{ece:.4f}**"
        if ece is not None
        else "- ECE: n/a (no human correctness labels yet)",
        "",
        "## Per-metric IRR",
        "",
    ]
    for metric, stats in irr_by_metric.items():
        lines.append(f"### {metric}")
        lines.append("")
        annotators = stats.get("annotators", [])
        lines.append(f"- Annotators: {', '.join(f'`{a}`' for a in annotators) if annotators else '(none)'}")
        n = stats.get("n_common_traces")
        lines.append(f"- Items scored by both: {n if n is not None else 'n/a'}")
        kappa = stats.get("cohens_kappa")
        r = stats.get("pearson_r")
        mad = stats.get("mean_absolute_disagreement")
        lines.append(f"- Cohen's kappa (discretized): **{kappa}**" if kappa is not None else "- Cohen's kappa: n/a")
        lines.append(f"- Pearson r (continuous): **{r}**" if r is not None else "- Pearson r: n/a")
        lines.append(f"- Mean absolute disagreement: {mad}" if mad is not None else "- Mean absolute disagreement: n/a")
        note = stats.get("note")
        if note:
            lines.append(f"- Note: {note}")
        dist = stats.get("disagreement_distribution", {})
        if dist:
            lines.append("")
            lines.append("Disagreement distribution (|score_A − score_B|):")
            lines.append("")
            lines.append("| Bin | Count |")
            lines.append("|-----|-------|")
            for bin_label, count in dist.items():
                if count > 0:
                    lines.append(f"| {bin_label} | {count} |")
        lines.append("")

    lines += [
        "## Interpretation guide",
        "",
        "| Kappa | Interpretation |",
        "|-------|----------------|",
        "| < 0.2 | Slight agreement — rubric anchors unclear; refine before Week 11 |",
        "| 0.2–0.4 | Fair agreement — targeted calibration session recommended |",
        "| 0.4–0.6 | Moderate agreement — acceptable for iteration signal |",
        "| 0.6–0.8 | Substantial agreement — rubric is working well |",
        "| > 0.8 | Near-perfect agreement |",
        "",
        "Items with mean absolute disagreement > 0.3 are rubric refinement candidates.",
        "Run `scripts/compute_automated_human_divergence.py` next to see where",
        "Layer 1 and Layer 2 disagree most.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset-name", default=_DEFAULT_DATASET, help="Langfuse dataset name")
    p.add_argument("--run-name", required=True, help="Langfuse dataset run name (e.g. v1-baseline)")
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
        help="Also write machine-readable JSON summary to this path",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print report; do not emit scores to Langfuse",
    )
    p.add_argument(
        "--automated-scores-json",
        type=Path,
        default=None,
        help="Path to qa_eval --json output; provides automated confidence scores for ECE",
    )
    args = p.parse_args(argv)

    from langfuse import Langfuse

    lf = Langfuse()

    log.info("fetching_run_scores", run_name=args.run_name, dataset_name=args.dataset_name)
    try:
        trace_scores = _fetch_run_scores(lf, args.dataset_name, args.run_name)
    except Exception as exc:  # noqa: BLE001
        log.error("fetch_failed", error=str(exc))
        lf.shutdown()
        return 1

    if not trace_scores:
        print(f"No traces found for run {args.run_name!r} in dataset {args.dataset_name!r}.", file=sys.stderr)
        lf.shutdown()
        return 1

    human_scores = _filter_human_scores(trace_scores)
    log.info("human_scores_found", n_traces=len(human_scores))

    if not human_scores:
        print("No human Layer 2 scores found. Humans must score in Langfuse UI first.", file=sys.stderr)
        lf.shutdown()
        return 0

    # IRR per metric
    irr_by_metric: dict[str, dict[str, Any]] = {
        metric: _compute_metric_irr(human_scores, metric) for metric in sorted(LAYER2_METRIC_NAMES)
    }

    # Aggregates per trace
    aggregates = _compute_aggregates(human_scores)

    # Human correctness composite
    hcc_vals: list[float] = []
    for _trace_id, agg in aggregates.items():
        c = agg.get("correctness_aggregate")
        dr = agg.get("decision_relevance_aggregate")
        fq = agg.get("followup_quality_aggregate")
        hcc_item = human_correctness_composite(correctness=c, decision_relevance=dr, followup_quality=fq)
        if hcc_item is not None:
            hcc_vals.append(hcc_item)
    mean_hcc = sum(hcc_vals) / len(hcc_vals) if hcc_vals else None

    # ECE — requires automated confidence scores
    trace_automated: dict[str, float] = {}
    if args.automated_scores_json and args.automated_scores_json.exists():
        payload = json.loads(args.automated_scores_json.read_text(encoding="utf-8"))
        for it in payload.get("items", []):
            tid = str(it.get("trace_id") or "")
            conf = (it.get("scores") or {}).get("confidence_self_consistency")
            if tid and isinstance(conf, (int, float)):
                trace_automated[tid] = float(conf)
    ece = _compute_run_ece(human_scores, trace_automated) if trace_automated else None

    # Render report
    report_md = _render_report(args.run_name, irr_by_metric, aggregates, ece, mean_hcc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report_md, encoding="utf-8")
    print(f"Wrote {args.output}")

    if args.json_output:
        summary = {
            "run_name": args.run_name,
            "n_traces_with_human_scores": len(human_scores),
            "irr_by_metric": irr_by_metric,
            "mean_human_correctness_composite": mean_hcc,
            "mean_confidence_ece": ece,
            "aggregates": {tid: {k: v for k, v in agg.items() if v is not None} for tid, agg in aggregates.items()},
        }
        args.json_output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Wrote {args.json_output}")

    # Emit aggregate scores to Langfuse
    if not args.dry_run:
        for trace_id, agg_scores in aggregates.items():
            _emit_scores(lf, trace_id, agg_scores)

        # Run-level scores
        if mean_hcc is not None:
            try:
                lf.score(
                    trace_id=None,
                    name="mean_human_correctness_composite",
                    value=float(mean_hcc),
                    comment=f"n={len(hcc_vals)} traces; compute_layer2_irr.py",
                    data_type="NUMERIC",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("run_level_score_emit_failed", name="mean_human_correctness_composite", error=str(exc))

        if ece is not None:
            try:
                lf.score(
                    trace_id=None,
                    name="mean_confidence_ece",
                    value=float(ece),
                    comment="ECE (lower is better); confidence vs human correctness; compute_layer2_irr.py",
                    data_type="NUMERIC",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("run_level_score_emit_failed", name="mean_confidence_ece", error=str(exc))

        log.info("scores_emitted", n_traces=len(aggregates))
    else:
        print("(dry-run: Langfuse score emission skipped)")

    # Print summary
    print(f"\nRun: {args.run_name}")
    print(f"Traces with human scores: {len(human_scores)}")
    if mean_hcc is not None:
        print(f"Mean human correctness composite: {mean_hcc:.4f}")
    if ece is not None:
        print(f"ECE (lower is better): {ece:.4f}")
    for metric, stats in irr_by_metric.items():
        kappa = stats.get("cohens_kappa")
        r = stats.get("pearson_r")
        mad = stats.get("mean_absolute_disagreement")
        print(f"  {metric}: kappa={kappa} pearson_r={r} mad={mad}")

    lf.flush()
    lf.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
