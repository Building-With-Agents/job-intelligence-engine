"""Voice layer: natural language answer from `EvidenceBundle`.

Grounded synthesis for Ask the Data (GitHub #117). See
``.cursor/rules/analytics-qna-synthesis.mdc``. USD amounts use
``common.llm_adapter.compute_extraction_cost`` / adapter-reported costs only.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog

from analytics.query_engine.constants import (
    CONFIDENCE_TRANSPARENCY_THRESHOLD,
    VOLUME_WARNING_POSTING_THRESHOLD,
)
from analytics.query_engine.schemas import (
    CostLedger,
    EvidenceBundle,
    EvidenceCitation,
    LLMCallCost,
    SynthesisResponse,
)
from common.llm_adapter import complete, compute_extraction_cost

log = structlog.get_logger()

AGENT_SYNTHESIS = "analytics-qna-synthesis"
AGENT_FOLLOWUP = "analytics-qna-followup"

_DEFAULT_REFUSAL = "Insufficient evidence to produce a grounded answer."
_SAFE_FALLBACK_ANSWER = (
    "A narrative summary could not be generated for this query. "
    "You may still review the cited facts and period coverage below."
)


def _transparency_flags(bundle: EvidenceBundle) -> dict[str, Any]:
    confidence_flagged_low = bundle.blended_confidence < CONFIDENCE_TRANSPARENCY_THRESHOLD
    volume_flagged_low = (
        bundle.volume_posting_count is not None
        and bundle.volume_posting_count < VOLUME_WARNING_POSTING_THRESHOLD
    )
    volume_warning: str | None = None
    if volume_flagged_low:
        volume_warning = (
            "Posting volume supporting this answer is below the transparency "
            f"threshold ({VOLUME_WARNING_POSTING_THRESHOLD}); treat findings as directional."
        )
    return {
        "confidence_flagged_low": confidence_flagged_low,
        "volume_flagged_low": volume_flagged_low,
        "volume_warning": volume_warning,
    }


def _cost_breakdown_usd(ledger: CostLedger) -> dict[str, float]:
    out: dict[str, float] = {}
    for leg in ledger.legs:
        out[leg.leg] = round(out.get(leg.leg, 0.0) + leg.cost_usd, 6)
    return out


def _leg_cost_usd(result: dict[str, Any], model_for_tier: str) -> float:
    reported = float(result.get("cost_usd") or 0.0)
    if reported > 0:
        return reported
    inp = int(result.get("input_tokens") or 0)
    out_tok = int(result.get("output_tokens") or 0)
    if inp + out_tok == 0:
        return 0.0
    return float(compute_extraction_cost(inp, out_tok, model_for_tier))


def _shape_from_invoke_skills(prompt: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Map ``invoke_skills_llm`` metadata to the dict shape used by ``complete()``."""
    success = bool(meta.get("success")) and not bool(meta.get("extraction_failed"))
    if not success:
        return {
            "content": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": float(meta.get("cost_usd") or 0.0),
            "model_tier": "azure",
            "success": False,
            "extraction_failed": True,
            "model": meta.get("model"),
        }
    input_tokens = max(1, len(prompt) // 4)
    output_tokens = max(0, len(text) // 4)
    tokens_used = int(meta.get("tokens_used") or 0)
    if tokens_used > 0:
        total_est = input_tokens + output_tokens
        if total_est > tokens_used:
            factor = tokens_used / total_est
            input_tokens = max(1, int(input_tokens * factor))
            output_tokens = max(0, tokens_used - input_tokens)
        elif total_est < tokens_used:
            output_tokens = max(0, tokens_used - input_tokens)
    model_name = str(meta.get("model") or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or "")
    cost_usd = float(meta.get("cost_usd") or 0.0)
    if cost_usd <= 0 and (input_tokens or output_tokens):
        cost_usd = float(compute_extraction_cost(input_tokens, output_tokens, model_name))
    return {
        "content": text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "model_tier": "azure",
        "success": True,
        "extraction_failed": False,
        "model": model_name or None,
    }


def _invoke_qna_completion(
    prompt: str,
    *,
    agent_name: str,
    model: str | None = None,
    max_tokens: int = 800,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Single completion: ``llm_adapter.complete`` except Azure OpenAI via ``llm_client``.

    Lazy-imports ``llm_client`` for Azure to avoid import cycles.
    """
    provider = os.getenv("LLM_PROVIDER", "azure_openai").strip().lower()
    if provider == "azure_openai":
        from common.llm_client import invoke_skills_llm

        text, meta = invoke_skills_llm(prompt, agent_name=agent_name)
        return _shape_from_invoke_skills(prompt, text, meta)
    return complete(
        prompt,
        agent_name=agent_name,
        model=model,
        max_tokens=max_tokens,
        correlation_id=correlation_id,
    )


def _synthesis_model() -> str | None:
    return os.getenv("ANALYTICS_QNA_SYNTHESIS_MODEL") or os.getenv(
        "EXTRACTION_MODEL_SKILLS",
        "claude-sonnet-4-5",
    )


def _followup_model() -> str | None:
    return os.getenv("ANALYTICS_QNA_FOLLOWUP_MODEL", "claude-haiku-4-5")


def _model_for_cost(result: dict[str, Any], fallback: str | None) -> str:
    return str(result.get("model") or fallback or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or "")


def _facts_payload(bundle: EvidenceBundle) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in bundle.facts:
        rows.append(
            {
                "citation_id": f.citation_id,
                "summary": f.summary,
                "source_table": f.source_table,
                "supporting_count": f.supporting_count,
                "time_period": f.time_period,
            }
        )
    return rows


def _build_main_prompt(user_query: str, intent_label: str, bundle: EvidenceBundle) -> str:
    facts_json = json.dumps(_facts_payload(bundle), ensure_ascii=False)
    payload = {
        "user_query": user_query,
        "intent_label": intent_label,
        "period_coverage": bundle.period_coverage,
        "data_sufficiency": str(bundle.sufficiency.value),
        "citeable_facts_json": facts_json,
    }
    instructions = (
        "You are an analytics assistant. Write a concise, professional answer for workforce stakeholders.\n"
        "Rules:\n"
        "- Use ONLY information supported by citeable_facts_json and period_coverage. "
        "Do not invent statistics, employers, or time ranges.\n"
        "- You may paraphrase the summary lines; do not add numbers absent from the facts.\n"
        "- If facts are thin, keep the answer short and explicitly cautious.\n"
        "- Do not include markdown code fences.\n"
    )
    return instructions + "Context JSON (for grounding):\n" + json.dumps(payload, ensure_ascii=False)


def _build_followup_prompt(
    user_query: str,
    intent_label: str,
    bundle: EvidenceBundle,
    answer_text: str,
) -> str:
    facts_json = json.dumps(_facts_payload(bundle), ensure_ascii=False)
    return (
        "Propose 2 to 3 short follow-up questions the user might ask next. "
        "They must be specific to the intent, facts, and answer below—not generic chat.\n"
        "Return ONLY a JSON array of strings, e.g. [\"...\",\"...\"]. No markdown.\n\n"
        f"intent_label: {intent_label}\n"
        f"user_query: {user_query}\n"
        f"period_coverage: {bundle.period_coverage}\n"
        f"citeable_facts_json: {facts_json}\n"
        f"answer_text: {answer_text}\n"
    )


def _parse_followup_json(content: str) -> list[str]:
    text = (content or "").strip()
    if not text:
        return []
    # strip optional markdown code block
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        text = m.group(0)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out[:3]


def _append_leg(
    ledger: CostLedger,
    leg: str,
    result: dict[str, Any],
    *,
    model_fallback: str | None,
) -> None:
    model_for_cost = _model_for_cost(result, model_fallback)
    cost = _leg_cost_usd(result, model_for_cost)
    ledger.add_leg(
        LLMCallCost(
            leg=leg,
            cost_usd=cost,
            input_tokens=int(result.get("input_tokens") or 0),
            output_tokens=int(result.get("output_tokens") or 0),
            model=result.get("model") or model_fallback,
        )
    )


def synthesize_answer(
    bundle: EvidenceBundle,
    *,
    user_query: str,
    intent_label: str,
    cost_ledger: CostLedger | None = None,
) -> SynthesisResponse:
    """Produce grounded answer text, follow-ups, and cost rollups.

    Uses Sonnet-class (or deployment default) for main text and Haiku-class for
    follow-ups when ``LLM_PROVIDER=anthropic``; Azure uses the shared LangChain
    path via ``invoke_skills_llm``. Only paraphrase facts on ``bundle``.

    See GitHub #117 and ``.cursor/rules/analytics-qna-synthesis.mdc``.
    """
    ledger = (
        CostLedger(legs=[LLMCallCost.model_validate(x.model_dump()) for x in cost_ledger.legs])
        if cost_ledger
        else CostLedger()
    )
    flags = _transparency_flags(bundle)
    citations: list[EvidenceCitation] = list(bundle.facts)
    periods_described = bundle.period_coverage
    confidence = bundle.blended_confidence
    conf_expl = bundle.confidence_explanation.strip() or None

    if bundle.refuse_synthesis:
        log.info(
            "analytics_qna_synthesis_refused",
            intent_label=intent_label,
            sufficiency=str(bundle.sufficiency),
        )
        return SynthesisResponse(
            answer_text="",
            citations=citations,
            periods_described=periods_described,
            confidence=confidence,
            confidence_flagged_low=flags["confidence_flagged_low"],
            confidence_explanation=conf_expl,
            volume_flagged_low=flags["volume_flagged_low"],
            volume_warning=flags["volume_warning"],
            refused=True,
            refusal_message=bundle.refusal_reason or _DEFAULT_REFUSAL,
            follow_up_questions=[],
            total_cost_usd=ledger.total_usd(),
            cost_breakdown_usd=_cost_breakdown_usd(ledger),
        )

    main_prompt = _build_main_prompt(user_query, intent_label, bundle)
    provider = os.getenv("LLM_PROVIDER", "azure_openai").strip().lower()
    syn_model = _synthesis_model() if provider == "anthropic" else None
    main_result = _invoke_qna_completion(
        main_prompt,
        agent_name=AGENT_SYNTHESIS,
        model=syn_model,
        max_tokens=800,
        correlation_id=None,
    )
    _append_leg(ledger, "synthesis", main_result, model_fallback=syn_model)

    main_ok = bool(main_result.get("success")) and not bool(main_result.get("extraction_failed"))
    answer_text = (main_result.get("content") or "").strip()
    if not main_ok or not answer_text:
        log.warning(
            "analytics_qna_synthesis_llm_degraded",
            intent_label=intent_label,
            extraction_failed=not main_ok,
        )
        return SynthesisResponse(
            answer_text=_SAFE_FALLBACK_ANSWER,
            citations=citations,
            periods_described=periods_described,
            confidence=confidence,
            confidence_flagged_low=flags["confidence_flagged_low"],
            confidence_explanation=conf_expl,
            volume_flagged_low=flags["volume_flagged_low"],
            volume_warning=flags["volume_warning"],
            refused=False,
            refusal_message=None,
            follow_up_questions=[],
            total_cost_usd=ledger.total_usd(),
            cost_breakdown_usd=_cost_breakdown_usd(ledger),
        )

    follow_prompt = _build_followup_prompt(user_query, intent_label, bundle, answer_text)
    fu_model = _followup_model() if provider == "anthropic" else None
    fu_result = _invoke_qna_completion(
        follow_prompt,
        agent_name=AGENT_FOLLOWUP,
        model=fu_model,
        max_tokens=400,
        correlation_id=None,
    )
    _append_leg(ledger, "follow_up", fu_result, model_fallback=fu_model)

    fu_ok = bool(fu_result.get("success")) and not bool(fu_result.get("extraction_failed"))
    follow_ups = _parse_followup_json(str(fu_result.get("content") or "")) if fu_ok else []

    log.info(
        "analytics_qna_synthesis_ok",
        intent_label=intent_label,
        follow_up_count=len(follow_ups),
        cost_usd=ledger.total_usd(),
    )

    return SynthesisResponse(
        answer_text=answer_text,
        citations=citations,
        periods_described=periods_described,
        confidence=confidence,
        confidence_flagged_low=flags["confidence_flagged_low"],
        confidence_explanation=conf_expl,
        volume_flagged_low=flags["volume_flagged_low"],
        volume_warning=flags["volume_warning"],
        refused=False,
        refusal_message=None,
        follow_up_questions=follow_ups,
        total_cost_usd=ledger.total_usd(),
        cost_breakdown_usd=_cost_breakdown_usd(ledger),
    )
