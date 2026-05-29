"""Voice layer: natural language answer from `EvidenceBundle`.

Grounded synthesis for Ask the Data (GitHub #117). See
``.cursor/rules/analytics-qna-synthesis.mdc``. USD amounts use
``common.llm_adapter.compute_extraction_cost`` / adapter-reported costs only.

JIE #258 — per-stage Langfuse observations: the main synthesis call and the
follow-up generation call are each wrapped with ``@observe(as_type="generation")``
so they appear as separate named Langfuse generations inside the parent Q&A trace,
enabling per-stage cost attribution and latency breakdown without changing the
public ``synthesize_answer`` signature.
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
from analytics.query_engine.langfuse_utils import lf_context as langfuse_context
from analytics.query_engine.langfuse_utils import lf_observe as _lf_observe
from analytics.query_engine.langfuse_utils import report_langfuse_usage
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


def _build_main_prompt(
    user_query: str,
    intent_label: str,
    bundle: EvidenceBundle,
    *,
    prior_turns_context: str | None = None,
) -> str:
    facts_json = json.dumps(_facts_payload(bundle), ensure_ascii=False)
    payload: dict[str, Any] = {
        "user_query": user_query,
        "intent_label": intent_label,
        "period_coverage": bundle.period_coverage,
        "data_sufficiency": str(bundle.sufficiency.value),
        "citeable_facts_json": facts_json,
    }
    ctx = (prior_turns_context or "").strip()
    if ctx:
        payload["prior_conversation"] = (
            "Earlier turns in this session (for continuity only; do not treat as new evidence): " + ctx
        )
    trend_clause = ""
    if intent_label in ("trend", "role_evolution"):
        trend_clause = (
            "- This question is trend or role_evolution: when citeable_facts_json includes "
            "time-bucketed demand, velocity, or role snapshots, name the direction of change "
            "(up / down / flat / mixed) and tie it to the cited counts — do not hand-wave.\n"
        )
    comparison_clause = ""
    if intent_label == "comparison":
        comparison_clause = (
            "- This question is a comparison: when citeable_facts_json includes counts for "
            "two or more skills, sectors, or temporal periods, state the magnitude difference "
            "(e.g. 'X has 3× more postings than Y') and identify the leader. "
            "If only one side has data, be explicit about which side is absent and why "
            "(e.g. 'no demand data found for Y in the current aggregate window'). "
            "Do not refuse or hedge when the data is thin — use it with appropriate caveats.\n"
        )
    instructions = (
        "You are an analytics assistant. Write a concise, professional answer for workforce stakeholders.\n"
        "Rules:\n"
        "- Use ONLY information supported by citeable_facts_json and period_coverage. "
        "Do not invent statistics, employers, or time ranges.\n"
        "- If prior_conversation is present, you may use it only to connect this answer to the thread "
        '(e.g. "as we discussed" / same geography); still ground all numbers in citeable_facts_json.\n'
        "- You may paraphrase the summary lines; do not add numbers absent from the facts.\n"
        "- If facts are thin (and this is not a comparison question), keep the answer short and explicitly cautious.\n"
        "- Salary facts: if citeable_facts_json shows salary amounts without an explicit currency "
        "code on that line (for example `salary=50,000–70,000` with no trailing ISO code), "
        "state the amounts as plain numbers only — do not assume USD or any other currency.\n"
        "- Do not include markdown code fences.\n"
        f"{trend_clause}"
        f"{comparison_clause}"
    )
    return instructions + "Context JSON (for grounding):\n" + json.dumps(payload, ensure_ascii=False)


def _build_main_prompt_retry(
    user_query: str,
    intent_label: str,
    bundle: EvidenceBundle,
    unsupported_tokens: tuple[str, ...],
    *,
    prior_turns_context: str | None = None,
) -> str:
    base = _build_main_prompt(user_query, intent_label, bundle, prior_turns_context=prior_turns_context)
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
    *,
    prior_turns_context: str | None = None,
) -> str:
    facts_json = json.dumps(_facts_payload(bundle), ensure_ascii=False)
    prior = (prior_turns_context or "").strip()
    block = f"prior_conversation: {prior}\n" if prior else ""
    return (
        "Propose 2 to 3 short follow-up questions the user might ask next. "
        "They must be specific to the intent, facts, and answer below—not generic chat.\n"
        'Return ONLY a JSON array of strings, e.g. ["...","..."]. No markdown.\n\n'
        f"intent_label: {intent_label}\n"
        f"user_query: {user_query}\n"
        f"{block}"
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


@_lf_observe(as_type="generation", name="synthesis")
def _call_synthesis_llm(
    prompt: str,
    *,
    intent_label: str,
    max_tokens: int = 800,
) -> dict[str, Any]:
    """Inner synthesis LLM call; wrapped as a named Langfuse generation (JIE #258).

    Exposes input prompt and raw LLM output so the generation observation carries
    per-call token usage and model metadata in the Langfuse trace.
    """
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"agent_name": AGENT_SYNTHESIS, "intent_label": intent_label},
    )
    result = complete(prompt, agent_name=AGENT_SYNTHESIS, role="synthesis", max_tokens=max_tokens)
    report_langfuse_usage(result)
    langfuse_context.update_current_observation(output=result.get("content") or "")
    return result


@_lf_observe(as_type="generation", name="follow_up_generation")
def _call_followup_llm(
    prompt: str,
    *,
    intent_label: str,
    max_tokens: int = 400,
) -> dict[str, Any]:
    """Inner follow-up LLM call; wrapped as a named Langfuse generation (JIE #258).

    Exposes input prompt and raw LLM output so the generation observation carries
    per-call token usage and model metadata in the Langfuse trace.
    """
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"agent_name": AGENT_FOLLOWUP, "intent_label": intent_label},
    )
    result = complete(prompt, agent_name=AGENT_FOLLOWUP, role="classification", max_tokens=max_tokens)
    report_langfuse_usage(result)
    langfuse_context.update_current_observation(output=result.get("content") or "")
    return result


def synthesize_answer(
    bundle: EvidenceBundle,
    *,
    user_query: str,
    intent_label: str,
    cost_ledger: CostLedger | None = None,
    prior_turns_context: str | None = None,
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
            sql_execution_error_detail=bundle.sql_execution_error_detail,
            follow_up_questions=[],
            total_cost_usd=ledger.total_usd(),
            cost_breakdown_usd=_cost_breakdown_usd(ledger),
        )

    main_prompt = _build_main_prompt(user_query, intent_label, bundle, prior_turns_context=prior_turns_context)
    main_result = _call_synthesis_llm(main_prompt, intent_label=intent_label, max_tokens=800)
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
        retry_prompt = _build_main_prompt_retry(
            user_query,
            intent_label,
            bundle,
            gr.unsupported_tokens,
            prior_turns_context=prior_turns_context,
        )
        retry_result = _call_synthesis_llm(retry_prompt, intent_label=intent_label, max_tokens=800)
        append_leg_from_complete(ledger, "synthesis", retry_result, model_fallback=None)
        retry_ok = bool(retry_result.get("success")) and not bool(retry_result.get("extraction_failed"))
        retry_text = (retry_result.get("content") or "").strip()
        if retry_ok and retry_text:
            answer_text = retry_text
            gr = verify_answer_grounding(answer_text, bundle)
        if not gr.ok:
            answer_text = _GROUNDING_FALLBACK_ANSWER

    answer_text = prefix_period_coverage(answer_text, bundle.period_coverage)

    follow_prompt = _build_followup_prompt(
        user_query,
        intent_label,
        bundle,
        answer_text,
        prior_turns_context=prior_turns_context,
    )
    fu_result = _call_followup_llm(follow_prompt, intent_label=intent_label, max_tokens=400)
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
