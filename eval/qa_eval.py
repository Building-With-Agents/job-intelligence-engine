# ruff: noqa: T201
"""Golden-question Q&A eval: analytics + automated scores + optional Langfuse dataset runs.

The baseline composite (``v1-baseline``) is the mean of four per-item metrics:
``intent_accuracy``, ``evidence_citation``, ``confidence_flags``, ``latency_sla``.

When applicable, a fifth per-item score ``answerability`` is also emitted
(data-backed expected intents only; not part of the composite). Run-level
``mean_*`` includes ``mean_answerability`` only for items that were scored.

Usage (repo root, venv active)::

    python -m eval.qa_eval --prompt-version v1-baseline
    python -m eval.qa_eval --prompt-version v1-baseline --limit 3 --dry-run
    python -m eval.qa_eval --prompt-version v1-baseline --dry-run --json > summary.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

from analytics.query_engine.routing import run_analytics_qna  # noqa: E402
from common.data_store.database import session_scope  # noqa: E402
from eval.qa_eval_laborpulse_headers import laborpulse_analytics_query_headers  # noqa: E402
from eval.qa_scoring import (  # noqa: E402
    QAItemScores,
    composite_score,
    compute_item_scores,
    confusion_rows,
)

log = structlog.get_logger()

DEFAULT_DATASET_NAME = "LaborPulse Golden Questions"
DEFAULT_JSON_PATH = _REPO_ROOT / "eval" / "qa_golden_questions.json"
_REQUIRED = ("id", "question", "intent", "ideal_answer_summary", "must_include", "must_not_include", "difficulty")


def _validate_golden_row(item: dict[str, Any], idx: int) -> None:
    for f in _REQUIRED:
        if f not in item:
            raise ValueError(f"Item[{idx}] missing {f!r} (id={item.get('id')!r})")


def load_golden_questions(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError("Golden file must be a JSON array")
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"Item[{i}] must be an object")
        _validate_golden_row(row, i)
    return raw


def _resolve_question_input(item: Any) -> tuple[str, str, dict[str, Any]]:
    """Return (question, gq_id, metadata) from DatasetItem or LocalExperimentItem dict."""
    if isinstance(item, dict):
        inp = item.get("input")
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        gq_id = str(item.get("id") or meta.get("id") or "")
        q = str(inp.get("question") or "").strip() if isinstance(inp, dict) else str(inp or "").strip()
        return q, gq_id, meta
    inp = getattr(item, "input", None)
    meta = getattr(item, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = dict(meta) if hasattr(meta, "items") else {}
    gq_id = str(getattr(item, "id", "") or meta.get("id") or "")
    q = str(inp.get("question") or "").strip() if isinstance(inp, dict) else str(inp or "").strip()
    return q, gq_id, meta


def _merge_golden(golden_by_id: dict[str, dict[str, Any]], gq_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    base = dict(golden_by_id.get(gq_id) or {})
    merged = {**meta, **base}
    if "expected_intent" not in merged and "intent" in base:
        merged["expected_intent"] = base["intent"]
    return merged


def execute_qa_item(
    *,
    question: str,
    correlation_id: str,
    use_http: bool,
    analytics_base_url: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run analytics Q&A; return (response_dict, pipeline_error)."""
    if not question:
        return None, "empty_question"
    try:
        if use_http:
            url = f"{analytics_base_url.rstrip('/')}/analytics/query"
            headers = laborpulse_analytics_query_headers(x_request_id=correlation_id)
            with httpx.Client(timeout=120.0) as client:
                r = client.post(
                    url,
                    json={"question": question, "correlation_id": correlation_id},
                    headers=headers,
                )
            if r.status_code >= 400:
                return None, f"http_{r.status_code}"
            data = r.json()
            if not isinstance(data, dict):
                return None, "invalid_json_response"
            return data, None
        with session_scope() as session:
            resp = run_analytics_qna(session, question, correlation_id)
        return resp.model_dump(mode="json"), None
    except Exception as exc:  # noqa: BLE001
        log.warning("qa_eval_item_failed", error=str(exc), correlation_id=correlation_id)
        return None, f"{type(exc).__name__}: {exc}"


def _task_factory(
    *,
    prompt_version: str,
    golden_by_id: dict[str, dict[str, Any]],
    use_http: bool,
    analytics_base_url: str,
):
    def task(*, item: Any, **kwargs: Any) -> dict[str, Any]:
        question, gq_id, meta = _resolve_question_input(item)
        golden = _merge_golden(golden_by_id, gq_id, meta)
        cid = f"{gq_id}-{prompt_version}"
        t0 = time.perf_counter()
        response, err = execute_qa_item(
            question=question,
            correlation_id=cid,
            use_http=use_http,
            analytics_base_url=analytics_base_url,
        )
        latency = time.perf_counter() - t0
        return {
            "prompt_version": prompt_version,
            "gq_id": gq_id,
            "golden": golden,
            "response": response,
            "latency_seconds": latency,
            "pipeline_error": err,
            "llm_default": os.getenv("LLM_DEFAULT", ""),
            "llm_synthesis": os.getenv("LLM_SYNTHESIS", ""),
        }

    return task


def _answerability_run_summary(
    item_results: list,
) -> tuple[int, int, float | None]:
    """Count items with answerability eval, intent-only (no eval), and mean (if any)."""
    ab: list[float] = []
    skipped = 0
    for ir in item_results:
        has_ab = False
        for ev in getattr(ir, "evaluations", []) or []:
            name = getattr(ev, "name", None) or (ev.get("name") if isinstance(ev, dict) else None)
            val = getattr(ev, "value", None) if not isinstance(ev, dict) else ev.get("value")
            if name == "answerability" and isinstance(val, (int, float)):
                ab.append(float(val))
                has_ab = True
        if not has_ab:
            skipped += 1
    mean = sum(ab) / len(ab) if ab else None
    return len(ab), skipped, mean


def _evaluator_factory(sla_seconds: float | None):
    """Build Langfuse evaluator: core Evaluations (None scores skipped), plus answerability when scored.

    Infrastructure-excluded content metrics (JIE #263) produce no Evaluation rather than a 0.0
    value so they are absent from Langfuse score distributions rather than polluting them.
    """

    def combined_evaluator(
        *,
        input: Any,
        output: Any,
        expected_output: Any,
        metadata: dict[str, Any] | None,
        **kwargs: Any,
    ):
        from langfuse import Evaluation

        if not isinstance(output, dict):
            # Malformed harness output (not a pipeline failure) — emit latency only.
            return [Evaluation(name="latency_sla", value=0.0, comment="malformed task output")]
        golden = output.get("golden") or {}
        scores = compute_item_scores(
            golden=golden,
            response=output.get("response"),
            latency_seconds=float(output.get("latency_seconds") or 0.0),
            pipeline_error=output.get("pipeline_error"),
            sla_seconds=sla_seconds,
        )
        # Only emit Evaluations for scorable (non-None) values; Langfuse requires float.
        evals: list[Any] = []
        for name in ("intent_accuracy", "evidence_citation", "confidence_flags"):
            val = getattr(scores, name)
            if val is not None:
                evals.append(Evaluation(name=name, value=val, comment=scores.comments[name][:500]))
        evals.append(
            Evaluation(name="latency_sla", value=scores.latency_sla, comment=scores.comments["latency_sla"][:500])
        )
        if scores.answerability is not None:
            ab_c = (scores.comments.get("answerability") or "")[:500]
            evals.append(Evaluation(name="answerability", value=float(scores.answerability), comment=ab_c))
        return evals

    return combined_evaluator


def _run_evaluators_average() -> list:
    """Run-level means for the four core metrics, plus mean answerability when present.

    ``n_scored`` and ``n_excluded`` are included in each comment so readers know
    the denominator and how many items were excluded as infrastructure failures (JIE #263).
    """

    def run_mean(*, item_results: list, **kwargs: Any):
        from langfuse import Evaluation

        sums: dict[str, list[float]] = {
            "intent_accuracy": [],
            "evidence_citation": [],
            "confidence_flags": [],
            "latency_sla": [],
            "answerability": [],
        }
        for ir in item_results:
            for ev in getattr(ir, "evaluations", []) or []:
                name = getattr(ev, "name", None) or (ev.get("name") if isinstance(ev, dict) else None)
                val = getattr(ev, "value", None) if not isinstance(ev, dict) else ev.get("value")
                if name in sums and isinstance(val, (int, float)):
                    sums[name].append(float(val))
        n_total = len(item_results)
        out: list[Any] = []
        for k in ("intent_accuracy", "evidence_citation", "confidence_flags", "latency_sla"):
            vals = sums[k]
            if vals:
                n_excl = n_total - len(vals)
                cmt = f"n_scored={len(vals)} n_excluded={n_excl} n_total={n_total}"
                out.append(Evaluation(name=f"mean_{k}", value=sum(vals) / len(vals), comment=cmt))
        ab = sums["answerability"]
        n_scored, n_skip, _ = _answerability_run_summary(item_results)
        if ab:
            pr = sum(ab) / len(ab)
            cmt = f"n={n_scored} intent_only_skipped={n_skip} pass_rate={pr:.4f}"
            out.append(Evaluation(name="mean_answerability", value=pr, comment=cmt))
        return out

    return [run_mean]


def _local_answerability_summary(
    rows: list[tuple[str, QAItemScores, str | None]],
) -> dict[str, int | float]:
    """Per-run stats for data-backed (non-null) answerability; composite remains four metrics."""
    ab_vals: list[float] = []
    for r in rows:
        a = r[1].answerability
        if a is not None:
            ab_vals.append(float(a))
    n = len(rows)
    n_data = len(ab_vals)
    n_skip = n - n_data
    if not ab_vals:
        return {
            "mean": 0.0,
            "n_data_backed": 0,
            "n_intent_only_skipped": n_skip,
            "pass_rate": 0.0,
        }
    m = sum(ab_vals) / len(ab_vals)
    return {
        "mean": m,
        "n_data_backed": n_data,
        "n_intent_only_skipped": n_skip,
        "pass_rate": m,
    }


def _metric_mean(rows: list[tuple[str, QAItemScores, str | None]], key: str) -> tuple[float | None, int, int]:
    """Return (mean_or_None, n_scored, n_excluded) for a named metric across rows."""
    vals = [getattr(r[1], key) for r in rows if getattr(r[1], key) is not None]
    n_scored = len(vals)
    n_excl = len(rows) - n_scored
    mean = sum(vals) / n_scored if vals else None
    return mean, n_scored, n_excl


def print_console_summary(
    *,
    rows: list[tuple[str, QAItemScores, str | None]],
    worst_n: int = 8,
) -> None:
    """Print per-metric means (with n_scored / n_excluded) and worst items by composite."""
    if not rows:
        print("No items.")
        return
    n = len(rows)
    keys = ("intent_accuracy", "evidence_citation", "confidence_flags", "latency_sla")
    print("\n=== QA golden eval — means ===")
    for k in keys:
        mean, n_scored, n_excl = _metric_mean(rows, k)
        if mean is not None:
            print(f"  {k}: {mean:.4f}  (n_scored={n_scored}/{n} excluded={n_excl})")
        else:
            print(f"  {k}: — excluded all  (n_excluded={n_excl}/{n})")
    print(f"\nItems total: {n}")
    a_sum = _local_answerability_summary(rows)
    print("\n=== Answerability (harness data-backed expected intents; not in composite) ===")
    if a_sum["n_data_backed"]:
        print(
            f"  mean / pass rate: {a_sum['mean']:.4f}  "
            f"over n={a_sum['n_data_backed']}  intent-only skipped: {a_sum['n_intent_only_skipped']}"
        )
    else:
        print(f"  (no data-backed items)  intent-only / skipped: {a_sum['n_intent_only_skipped']}")

    def _fmt(v: float | None) -> str:
        return f"{v:.2f}" if v is not None else " — "

    ranked = sorted(rows, key=lambda r: composite_score(r[1]))
    print(f"\n=== Worst {worst_n} by composite (mean of four scores) ===")
    for gq_id, sc, err in ranked[:worst_n]:
        ce = err or ""
        print(
            f"  {gq_id}: composite={composite_score(sc):.3f} "
            f"i={_fmt(sc.intent_accuracy)} e={_fmt(sc.evidence_citation)} "
            f"c={_fmt(sc.confidence_flags)} l={sc.latency_sla:.2f} {ce[:60]}"
        )


def print_confusion(intent_pairs: list[tuple[str, str]]) -> None:
    c = confusion_rows(intent_pairs)
    print("\n=== Intent confusion (expected → predicted counts) ===")
    for (e, p), cnt in sorted(c.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {e!r} → {p!r}: {cnt}")


def run_local_loop(
    questions: list[dict[str, Any]],
    *,
    prompt_version: str,
    use_http: bool,
    analytics_base_url: str,
    sla_seconds: float | None,
) -> tuple[list[tuple[str, QAItemScores, str | None]], list[tuple[str, str]]]:
    """Run without Langfuse; return (rows for summary, intent pairs)."""
    out: list[tuple[str, QAItemScores, str | None]] = []
    intent_pairs: list[tuple[str, str]] = []

    for row in questions:
        gq_id = str(row["id"])
        q = str(row["question"])
        cid = f"{gq_id}-{prompt_version}"
        t0 = time.perf_counter()
        response, err = execute_qa_item(
            question=q,
            correlation_id=cid,
            use_http=use_http,
            analytics_base_url=analytics_base_url,
        )
        latency = time.perf_counter() - t0
        scores = compute_item_scores(
            golden=row,
            response=response,
            latency_seconds=latency,
            pipeline_error=err,
            sla_seconds=sla_seconds,
        )
        exp = str(row.get("intent") or "")
        pred = str((response or {}).get("classified_intent") or "other") if response else "other"
        intent_pairs.append((exp, pred))
        out.append((gq_id, scores, err))
    return out, intent_pairs


def _experiment_result_to_items(result: Any) -> list[dict[str, Any]]:
    """Serialize Langfuse ExperimentResult item_results to JSON-friendly dicts."""
    items_out: list[dict[str, Any]] = []
    for ir in result.item_results:
        gq_id = ""
        if isinstance(ir.item, dict):
            meta = ir.item.get("metadata") or {}
            gq_id = str(ir.item.get("id") or meta.get("id") or "")
        else:
            gq_id = str(getattr(ir.item, "id", "") or "")
        ev_map: dict[str, Any] = {}
        for ev in ir.evaluations:
            name = getattr(ev, "name", None)
            val = getattr(ev, "value", None)
            if name:
                ev_map[str(name)] = val
        items_out.append({"gq_id": gq_id, "trace_id": ir.trace_id, "scores": ev_map})
    return items_out


def _build_local_experiment_data(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Langfuse LocalExperimentItem list mirroring upload_qa_dataset layout."""
    data: list[dict[str, Any]] = []
    for row in questions:
        data.append(
            {
                "input": {"question": row["question"]},
                "expected_output": {"ideal_answer_summary": row["ideal_answer_summary"]},
                "metadata": {
                    "id": row["id"],
                    "expected_intent": row["intent"],
                    "intent": row["intent"],
                    "context": row.get("context", ""),
                    "must_include": row["must_include"],
                    "must_not_include": row["must_not_include"],
                    "difficulty": row["difficulty"],
                    "ideal_answer_summary": row["ideal_answer_summary"],
                },
            }
        )
    return data


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run LaborPulse golden-question Q&A eval with Langfuse scores.")
    p.add_argument("--prompt-version", required=True, help="Run name / version tag (e.g. v1-baseline)")
    p.add_argument("--json-path", type=Path, default=DEFAULT_JSON_PATH, help="Golden questions JSON")
    p.add_argument("--dataset-name", default=DEFAULT_DATASET_NAME, help="Langfuse dataset name")
    p.add_argument("--limit", type=int, default=None, help="Evaluate only first N questions")
    p.add_argument("--dry-run", action="store_true", help="Do not call Langfuse; still run Q&A and print summary")
    p.add_argument(
        "--use-http",
        action="store_true",
        help="Call POST /analytics/query instead of in-process run_analytics_qna",
    )
    p.add_argument(
        "--analytics-base-url",
        default=os.getenv("ANALYTICS_QUERY_BASE_URL", "http://127.0.0.1:8000"),
        help="Base URL for analytics API (default env ANALYTICS_QUERY_BASE_URL)",
    )
    p.add_argument("--max-concurrency", type=int, default=2, help="Langfuse experiment concurrency")
    p.add_argument(
        "--local-experiment-only",
        action="store_true",
        help="Use Langfuse run_experiment with local JSON (traces+scores; no pre-existing dataset items)",
    )
    p.add_argument("--output-json", type=Path, default=None, help="Write run summary JSON to this file")
    p.add_argument(
        "--json",
        action="store_true",
        help="Print run summary JSON to stdout (suppresses human-readable report)",
    )
    p.add_argument("--worst-n", type=int, default=8, help="How many lowest composite items to print")
    args = p.parse_args(argv)

    json_path = args.json_path
    if not json_path.exists():
        log.error("json_missing", path=str(json_path))
        return 1

    try:
        all_questions = load_golden_questions(json_path)
    except ValueError as exc:
        log.error("json_invalid", error=str(exc))
        return 1

    golden_by_id = {str(r["id"]): r for r in all_questions}
    questions = all_questions[: max(0, args.limit)] if args.limit is not None else all_questions

    sla_seconds = float(os.getenv("QA_EVAL_LATENCY_SLA_SECONDS") or "45.0")

    if args.dry_run or not os.getenv("LANGFUSE_SECRET_KEY"):
        if not os.getenv("LANGFUSE_SECRET_KEY") and not args.dry_run:
            log.warning("langfuse_disabled_missing_secret", msg="Set LANGFUSE_SECRET_KEY or use --dry-run")
        rows, intent_pairs = run_local_loop(
            questions,
            prompt_version=args.prompt_version,
            use_http=args.use_http,
            analytics_base_url=args.analytics_base_url,
            sla_seconds=sla_seconds,
        )
        payload_local: dict[str, Any] = {
            "prompt_version": args.prompt_version,
            "dry_run": bool(args.dry_run),
            "langfuse": False,
            "items": [
                {
                    "gq_id": gid,
                    "scores": {
                        "intent_accuracy": sc.intent_accuracy,
                        "evidence_citation": sc.evidence_citation,
                        "confidence_flags": sc.confidence_flags,
                        "latency_sla": sc.latency_sla,
                        "answerability": sc.answerability,
                    },
                    "error": err,
                }
                for gid, sc, err in rows
            ],
        }
        if rows:
            keys = ("intent_accuracy", "evidence_citation", "confidence_flags", "latency_sla")
            payload_local["means"] = {
                k: round(m, 6) if (m := _metric_mean(rows, k)[0]) is not None else None
                for k in keys
            }
            payload_local["excluded_counts"] = {
                k: _metric_mean(rows, k)[2] for k in ("intent_accuracy", "evidence_citation", "confidence_flags")
            }
            payload_local["answerability_summary"] = _local_answerability_summary(rows)
            payload_local["intent_confusion"] = {
                f"{e}->{p}": c for (e, p), c in sorted(confusion_rows(intent_pairs).items())
            }

        if args.json:
            sys.stdout.write(json.dumps(payload_local, indent=2) + "\n")
        else:
            print_console_summary(rows=rows, worst_n=args.worst_n)
            print_confusion(intent_pairs)
        if args.output_json:
            args.output_json.write_text(json.dumps(payload_local, indent=2), encoding="utf-8")
            if not args.json:
                print(f"\nWrote {args.output_json}")
            else:
                print(f"Wrote {args.output_json}", file=sys.stderr)
        return 0

    from langfuse import Langfuse

    lf = Langfuse()
    task = _task_factory(
        prompt_version=args.prompt_version,
        golden_by_id=golden_by_id,
        use_http=args.use_http,
        analytics_base_url=args.analytics_base_url,
    )
    ev_fn = _evaluator_factory(sla_seconds)
    run_evals = _run_evaluators_average()
    desc = f"Golden QA eval prompt_version={args.prompt_version}"
    meta = {
        "prompt_version": args.prompt_version,
        "eval": "qa_golden",
        "llm_default": os.getenv("LLM_DEFAULT", ""),
        "llm_synthesis": os.getenv("LLM_SYNTHESIS", ""),
    }

    # Hosted dataset run only when using the full uploaded dataset (--limit uses local JSON experiment).
    use_hosted_dataset = not args.local_experiment_only and args.limit is None
    if args.limit is not None and not args.dry_run:
        log.warning(
            "limit_forces_local_langfuse_experiment",
            limit=args.limit,
            msg="Dataset run linking to LaborPulse Golden Questions requires full corpus; using local JSON for this run.",
        )

    try:
        if not use_hosted_dataset:
            data = _build_local_experiment_data(questions)
            result = lf.run_experiment(
                name=f"qa-golden-{args.prompt_version}",
                run_name=args.prompt_version,
                description=desc,
                data=data,
                task=task,
                evaluators=[ev_fn],
                run_evaluators=run_evals,
                max_concurrency=args.max_concurrency,
                metadata=meta,
            )
        else:
            dataset = lf.get_dataset(args.dataset_name)
            result = dataset.run_experiment(
                name=f"qa-golden-{args.prompt_version}",
                run_name=args.prompt_version,
                description=desc,
                task=task,
                evaluators=[ev_fn],
                run_evaluators=run_evals,
                max_concurrency=args.max_concurrency,
                metadata=meta,
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("langfuse_experiment_failed", error=str(exc))
        lf.flush()
        lf.shutdown()
        return 1

    items_out = _experiment_result_to_items(result)
    ab_vals: list[float] = []
    for it in items_out:
        av = (it.get("scores") or {}).get("answerability")
        if isinstance(av, (int, float)):
            ab_vals.append(float(av))
    n_items = len(items_out)
    answerability_summary: dict[str, int | float] = {
        "mean": (sum(ab_vals) / len(ab_vals)) if ab_vals else 0.0,
        "n_data_backed": len(ab_vals),
        "n_intent_only_skipped": n_items - len(ab_vals),
        "pass_rate": (sum(ab_vals) / len(ab_vals)) if ab_vals else 0.0,
    }
    payload_lf: dict[str, Any] = {
        "prompt_version": args.prompt_version,
        "dataset_run_id": result.dataset_run_id,
        "dataset_run_url": result.dataset_run_url,
        "answerability_summary": answerability_summary,
        "items": items_out,
    }
    if args.json:
        sys.stdout.write(json.dumps(payload_lf, indent=2) + "\n")
    else:
        print(result.format(include_item_results=True))
    if args.output_json:
        args.output_json.write_text(json.dumps(payload_lf, indent=2), encoding="utf-8")
        if not args.json:
            print(f"\nWrote {args.output_json}")
        else:
            print(f"Wrote {args.output_json}", file=sys.stderr)

    lf.flush()
    lf.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
