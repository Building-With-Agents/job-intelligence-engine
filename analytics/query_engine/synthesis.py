"""Voice layer: natural language answer from `EvidenceBundle`.

Grounded synthesis for Ask the Data (GitHub #117). See
``.cursor/rules/analytics-qna-synthesis.mdc``. USD amounts use
``common.llm_adapter.compute_extraction_cost`` / adapter-reported costs only.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from analytics.query_engine.constants import (
    CONFIDENCE_TRANSPARENCY_THRESHOLD,
    VOLUME_WARNING_POSTING_THRESHOLD,
)
from analytics.query_engine.grounding import (
    prefix_period_coverage,
    verify_answer_grounding,
)
from analytics.query_engine.ledger_utils import append_leg_from_complete
from analytics.query_engine.schemas import (
    CostLedger,
    DataSufficiency,
    EvidenceBundle,
    EvidenceCitation,
    LLMCallCost,
    SynthesisResponse,
)
from common.llm_adapter import complete

log = structlog.get_logger()

AGENT_SYNTHESIS = "analytics-qna-synthesis"
AGENT_FOLLOWUP = "analytics-qna-followup"

_DEFAULT_REFUSAL = "Insufficient evidence to produce a grounded answer."
_SAFE_FALLBACK_ANSWER = (
    "A narrative summary could not be generated for this query. "
    "You may still review the cited facts and period coverage below."
)
_GROUNDING_FALLBACK_ANSWER = (
    "The generated summary contained numeric claims that are not fully supported by "
    "the retrieved evidence. Review the citations below for verified figures; "
    "do not rely on any previously shown statistics."
)


def _transparency_flags(bundle: EvidenceBundle) -> dict[str, Any]:
    confidence_flagged_low = bundle.blended_confidence < CONFIDENCE_TRANSPARENCY_THRESHOLD
    volume_flagged_low = (
        bundle.sufficiency != DataSufficiency.NO_DATA
        and bundle.volume_posting_count is not None
        and bundle.volume_posting_count > 0
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


def _build_main_prompt_retry(
    user_query: str,
    intent_label: str,
    bundle: EvidenceBundle,
    unsupported_tokens: tuple[str, ...],
) -> str:
    base = _build_main_prompt(user_query, intent_label, bundle)
    bad = ", ".join(unsupported_tokens[:12])
    fix = (
        "\n\nYour previous draft used numbers not found in citeable_facts_json or period_coverage: "
        f"{bad}. Rewrite the answer using ONLY supported numbers; omit unsupported statistics.\n"
    )
    return base + fix


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
        'Return ONLY a JSON array of strings, e.g. ["...","..."]. No markdown.\n\n'
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


def synthesize_answer(
    bundle: EvidenceBundle,
    *,
    user_query: str,
    intent_label: str,
    cost_ledger: CostLedger | None = None,
) -> SynthesisResponse:
    """Produce grounded answer text, follow-ups, and cost rollups.

    Sonnet-tier (``LLM_SYNTHESIS``) for main answer; Haiku-tier (``LLM_DEFAULT``
    via ``role="classification"``) for follow-up questions. Routes via
    ``common.llm_adapter.complete(role=...)``. Only paraphrases facts on ``bundle``.

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
    main_result = complete(
        main_prompt,
        agent_name=AGENT_SYNTHESIS,
        role="synthesis",
        max_tokens=800,
    )
    append_leg_from_complete(ledger, "synthesis", main_result, model_fallback=None)

    main_ok = bool(main_result.get("success")) and not bool(main_result.get("extraction_failed"))
    answer_text = (main_result.get("content") or "").strip()
    if not main_ok or not answer_text:
        log.warning(
            "analytics_qna_synthesis_llm_degraded",
            intent_label=intent_label,
            extraction_failed=not main_ok,
        )
        safe_body = prefix_period_coverage(_SAFE_FALLBACK_ANSWER, bundle.period_coverage)
        return SynthesisResponse(
            answer_text=safe_body,
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

    gr = verify_answer_grounding(answer_text, bundle)
    if not gr.ok:
        log.warning(
            "analytics_qna_grounding_failed",
            intent_label=intent_label,
            unsupported_count=len(gr.unsupported_tokens),
            reason_code=gr.reason_code,
        )
        retry_prompt = _build_main_prompt_retry(user_query, intent_label, bundle, gr.unsupported_tokens)
        retry_result = complete(
            retry_prompt,
            agent_name=AGENT_SYNTHESIS,
            role="synthesis",
            max_tokens=800,
        )
        append_leg_from_complete(ledger, "synthesis", retry_result, model_fallback=None)
        retry_ok = bool(retry_result.get("success")) and not bool(retry_result.get("extraction_failed"))
        retry_text = (retry_result.get("content") or "").strip()
        if retry_ok and retry_text:
            answer_text = retry_text
            gr = verify_answer_grounding(answer_text, bundle)
        if not gr.ok:
            answer_text = _GROUNDING_FALLBACK_ANSWER

    answer_text = prefix_period_coverage(answer_text, bundle.period_coverage)

    follow_prompt = _build_followup_prompt(user_query, intent_label, bundle, answer_text)
    fu_result = complete(
        follow_prompt,
        agent_name=AGENT_FOLLOWUP,
        role="classification",
        max_tokens=400,
    )
    append_leg_from_complete(ledger, "follow_up", fu_result, model_fallback=None)

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
