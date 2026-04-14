#!/usr/bin/env python3
"""Demo CLI: wall-clock stages + SynthesisResponse JSON for analytics QnA (#117).

Week 8 voice layer metrics. Default uses fixture ``EvidenceBundle`` and mock LLM.
See ``.cursor/rules/analytics-qna-synthesis.mdc``.

Usage (repo root, venv active)::

    python scripts/demo_qna_synthesis_metrics.py
    python scripts/demo_qna_synthesis_metrics.py --full-pipeline
    ANALYTICS_QNA_LIVE=1 python scripts/demo_qna_synthesis_metrics.py --live
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from typing import Any
from unittest import mock

# Allow ``python scripts/...`` from repo root
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _json_safe(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description="Analytics QnA synthesis metrics demo (#117).")
    parser.add_argument(
        "--use-fixture-bundle",
        action="store_true",
        default=True,
        help="Use fixture EvidenceBundle (default).",
    )
    parser.add_argument(
        "--no-use-fixture-bundle",
        action="store_false",
        dest="use_fixture_bundle",
        help="Disable fixture bundle (requires --full-pipeline).",
    )
    parser.add_argument(
        "--full-pipeline",
        action="store_true",
        help="Call build_evidence_bundle(fixture QueryResultPayload); falls back with message if stubbed.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow non-mock LLM (requires ANALYTICS_QNA_LIVE=1 and configured provider credentials).",
    )
    args = parser.parse_args()

    if args.live:
        if os.getenv("ANALYTICS_QNA_LIVE", "").strip() != "1":
            print(
                "Refusing --live: set ANALYTICS_QNA_LIVE=1 after confirming credentials and spend.",
                file=sys.stderr,
            )
            return 2
        warnings.warn(
            "Live LLM mode may incur cost; ensure LLM_PROVIDER and API keys are intended for this run.",
            stacklevel=1,
        )
    else:
        os.environ["LLM_PROVIDER"] = "mock"

    from analytics.query_engine.fixtures import (
        sample_evidence_bundle_adequate,
        sample_query_result_payload_ok,
    )
    from analytics.query_engine.schemas import CostLedger, EvidenceBundle, LLMCallCost
    from analytics.query_engine.synthesis import AGENT_FOLLOWUP, AGENT_SYNTHESIS, synthesize_answer

    timings: dict[str, float] = {}
    bundle: EvidenceBundle | None = None
    cost_ledger: CostLedger | None = None
    t0 = time.perf_counter()

    if args.full_pipeline:
        t_ev = time.perf_counter()
        try:
            from analytics.query_engine.evidence import build_evidence_bundle

            payload = sample_query_result_payload_ok()
            bundle = build_evidence_bundle(payload)
        except NotImplementedError:
            print(
                json.dumps(
                    {
                        "error": "build_evidence_bundle is not implemented yet",
                        "hint": "Re-run with default fixture mode: omit --full-pipeline or use --use-fixture-bundle.",
                    },
                    indent=2,
                )
            )
            return 1
        timings["evidence_bundle_ms"] = (time.perf_counter() - t_ev) * 1000.0
        cost_ledger = CostLedger(
            legs=[
                LLMCallCost(leg="intent_classification", cost_usd=0.0001, input_tokens=10, output_tokens=2),
                LLMCallCost(leg="sql_generation", cost_usd=0.0002, input_tokens=20, output_tokens=4),
            ]
        )
        user_q = payload.request.query
        intent = payload.intent_label
    elif args.use_fixture_bundle:
        t_fix = time.perf_counter()
        bundle = sample_evidence_bundle_adequate()
        cost_ledger = CostLedger(
            legs=[
                LLMCallCost(leg="intent_classification", cost_usd=0.0001, input_tokens=10, output_tokens=2),
            ]
        )
        timings["fixture_bundle_ms"] = (time.perf_counter() - t_fix) * 1000.0
        user_q = "What is the median salary for software roles?"
        intent = "aggregate_salary"
    else:
        print(json.dumps({"error": "Provide --full-pipeline or keep --use-fixture-bundle (default)."}, indent=2))
        return 2

    assert bundle is not None
    t_syn = time.perf_counter()

    def _demo_llm(prompt: str, *, agent_name: str, model=None, max_tokens: int = 800, correlation_id=None):
        if agent_name == AGENT_SYNTHESIS:
            return {
                "content": (
                    "Across the cited window, the evidence points to a median salary of USD 72,000 "
                    "from 84 postings in scope; treat this as a snapshot for the stated period only."
                ),
                "input_tokens": 120,
                "output_tokens": 60,
                "cost_usd": 0.001,
                "success": True,
                "extraction_failed": False,
                "model": "demo-stub",
            }
        if agent_name == AGENT_FOLLOWUP:
            return {
                "content": (
                    '["How does this median compare by seniority?", '
                    '"Which employers drive the median in this window?", '
                    '"What is the posting trend for the next quarter?"]'
                ),
                "input_tokens": 80,
                "output_tokens": 40,
                "cost_usd": 0.0005,
                "success": True,
                "extraction_failed": False,
                "model": "demo-stub",
            }
        raise RuntimeError(f"unexpected agent_name={agent_name!r}")

    with mock.patch("common.llm_adapter.log_extraction_event", lambda *_a, **_k: None):
        if args.live:
            result = synthesize_answer(
                bundle,
                user_query=user_q,
                intent_label=intent,
                cost_ledger=cost_ledger,
            )
        else:
            with mock.patch(
                "analytics.query_engine.synthesis._invoke_qna_completion",
                side_effect=_demo_llm,
            ):
                result = synthesize_answer(
                    bundle,
                    user_query=user_q,
                    intent_label=intent,
                    cost_ledger=cost_ledger,
                )
    timings["end_to_end_ms"] = (time.perf_counter() - t0) * 1000.0

    out = {
        "timings_ms": timings,
        "synthesis_response": _json_safe(result),
        "total_cost_usd": result.total_cost_usd,
        "cost_breakdown_usd": result.cost_breakdown_usd,
    }
    print(json.dumps(out, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
