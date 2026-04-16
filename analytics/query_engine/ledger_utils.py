"""Shared CostLedger helpers for Q&A LLM legs (GitHub #117)."""

from __future__ import annotations

from typing import Any

from analytics.query_engine.schemas import CostLedger, LLMCallCost
from common.llm_adapter import compute_extraction_cost


def _model_for_cost(result: dict[str, Any], fallback: str | None) -> str:
    return str(result.get("model") or fallback or "")


def leg_cost_usd(result: dict[str, Any], model_for_tier: str) -> float:
    reported = float(result.get("cost_usd") or 0.0)
    if reported > 0:
        return reported
    inp = int(result.get("input_tokens") or 0)
    out_tok = int(result.get("output_tokens") or 0)
    if inp + out_tok == 0:
        return 0.0
    return float(compute_extraction_cost(inp, out_tok, model_for_tier))


def append_leg_from_complete(
    ledger: CostLedger,
    leg: str,
    result: dict[str, Any],
    *,
    model_fallback: str | None,
) -> None:
    """Append one ``LLMCallCost`` from a ``complete()`` result dict."""
    model_for_cost = _model_for_cost(result, model_fallback)
    cost = leg_cost_usd(result, model_for_cost)
    ledger.add_leg(
        LLMCallCost(
            leg=leg,
            cost_usd=cost,
            input_tokens=int(result.get("input_tokens") or 0),
            output_tokens=int(result.get("output_tokens") or 0),
            model=result.get("model") or model_fallback,
        )
    )
