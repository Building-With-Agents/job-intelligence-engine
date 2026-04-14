"""Turn tabular query results into narrative answer + evidence (LLM-backed)."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from common.llm_adapter import complete

_SYNTH_SYSTEM = """You are a workforce analytics assistant. Given JSON rows from a database query,
write a concise factual summary. Return JSON ONLY with keys:
answer (string),
evidence (array of {title, source, snippet}),
confidence (number 0-1),
follow_up_questions (array of up to 4 short strings).
Do not invent employers or job IDs not present in the rows."""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}\s*$", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def synthesize(
    question: str,
    rows: list[dict[str, Any]],
    correlation_id: str | None,
    *,
    prior_cost: float,
) -> tuple[str, list[dict[str, Any]], float, list[str], float]:
    """Return answer, evidence dicts, confidence, follow_ups, total_cost."""
    cid = correlation_id or str(uuid.uuid4())
    preview = rows[:30]
    prompt = (
        f"User question: {question}\n\n"
        f"Rows (JSON, max 30): {json.dumps(preview, default=str)[:12000]}"
    )
    out = complete(
        prompt,
        agent_name="analytics_query_synthesis",
        system=_SYNTH_SYSTEM,
        max_tokens=1200,
        correlation_id=cid,
        role="synthesis",
    )
    cost = prior_cost + float(out.get("cost_usd") or 0.0)
    if out.get("extraction_failed"):
        return (
            "The model could not synthesize an answer for this question.",
            [],
            0.0,
            [],
            cost,
        )

    data = _extract_json_object(out.get("content") or "")
    if not data:
        return (
            "Unable to parse synthesis output.",
            [{"title": "rows", "source": "query", "snippet": json.dumps(preview, default=str)[:2000]}],
            0.35,
            ["Try narrowing your question to a single metric or time window."],
            cost,
        )

    answer = str(data.get("answer") or "").strip()
    evidence = data.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = []
    evidence_out: list[dict[str, Any]] = []
    for item in evidence[:20]:
        if isinstance(item, dict):
            evidence_out.append(
                {
                    "title": str(item.get("title", ""))[:500],
                    "source": str(item.get("source", ""))[:500],
                    "snippet": str(item.get("snippet", ""))[:4000],
                }
            )
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    fup = data.get("follow_up_questions") or []
    if not isinstance(fup, list):
        fup = []
    follow = [str(x).strip() for x in fup[:6] if str(x).strip()]
    return answer, evidence_out, confidence, follow, cost
