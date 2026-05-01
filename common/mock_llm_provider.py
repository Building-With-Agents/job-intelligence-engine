"""Mock LLM provider for local development and trace generation.

Returns realistic extraction results from ground truth data
(eval/extraction_ground_truth.json) without calling any real LLM.
Generates proper token counts, costs, and latency for Langfuse traces.

Usage: set LLM_PROVIDER=mock in .env
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

_GROUND_TRUTH: list[dict] | None = None
_QA_GOLDEN: list[dict] | None = None
_GT_INDEX = 0

TSchema = TypeVar("TSchema", bound=BaseModel)

# ---------------------------------------------------------------------------
# Ground truth loading
# ---------------------------------------------------------------------------


def _load_ground_truth() -> list[dict]:
    """Load and cache ground truth records."""
    global _GROUND_TRUTH
    if _GROUND_TRUTH is None:
        gt_path = Path(__file__).parent.parent / "eval" / "extraction_ground_truth.json"
        with open(gt_path, encoding="utf-8") as f:
            _GROUND_TRUTH = json.load(f)
    return _GROUND_TRUTH


def _next_gt_record() -> dict:
    """Return next ground truth record (round-robin)."""
    global _GT_INDEX
    records = _load_ground_truth()
    record = records[_GT_INDEX % len(records)]
    _GT_INDEX += 1
    return record


def _load_qa_golden() -> list[dict]:
    global _QA_GOLDEN
    if _QA_GOLDEN is None:
        gt_path = Path(__file__).parent.parent / "eval" / "qa_golden_questions.json"
        with open(gt_path, encoding="utf-8") as f:
            _QA_GOLDEN = json.load(f)
    return _QA_GOLDEN


def _golden_row_for_user_question(user_question: str) -> dict | None:
    uq = " ".join((user_question or "").split())
    if len(uq) < 8:
        return None
    best: dict | None = None
    best_len = 0
    for row in _load_qa_golden():
        rq = " ".join(str(row.get("question", "")).split())
        if not rq:
            continue
        if (rq in uq or uq in rq) and len(rq) > best_len:
            best_len = len(rq)
            best = row
    return best


def _extract_intent_user_question(prompt: str) -> str:
    if "Current user question (this turn only):" in prompt:
        tail = prompt.split("Current user question (this turn only):", 1)[1].strip()
        return tail.split("\n", 1)[0].strip()
    if "User question:" in prompt:
        tail = prompt.split("User question:", 1)[1].strip()
        return tail.split("\n", 1)[0].strip()
    return ""


def _extract_synthesis_context_json(prompt: str) -> dict[str, Any] | None:
    marker = "Context JSON (for grounding):\n"
    if marker not in prompt:
        return None
    try:
        ctx = json.loads(prompt.split(marker, 1)[1].strip())
    except json.JSONDecodeError:
        return None
    return ctx if isinstance(ctx, dict) else None


def _mock_analytics_synthesis_answer(prompt: str) -> str | None:
    ctx = _extract_synthesis_context_json(prompt)
    if not ctx:
        return None
    uq = str(ctx.get("user_query") or "").strip()
    g = _golden_row_for_user_question(uq)
    if not g:
        return None
    parts: list[str] = []
    pc = str(ctx.get("period_coverage") or "").strip()
    if pc:
        parts.append(f"Data period: {pc}.")
    raw_facts = ctx.get("citeable_facts_json")
    try:
        facts = json.loads(raw_facts) if isinstance(raw_facts, str) else []
    except json.JSONDecodeError:
        facts = []
    if isinstance(facts, list):
        for fact in facts[:14]:
            if isinstance(fact, dict) and fact.get("summary"):
                parts.append(str(fact["summary"])[:520])
    for tok in g.get("must_include") or []:
        parts.append(str(tok).replace("_", " "))
    body = "\n\n".join(p for p in parts if p).strip()
    return body[:8000] if body else None


def _mock_curriculum_synthesis_answer(prompt: str) -> str | None:
    pl = prompt.lower()
    best: dict | None = None
    best_sc = 0
    for row in _load_qa_golden():
        if row.get("intent") != "curriculum":
            continue
        q = str(row.get("question", "")).lower()
        sc = sum(1 for w in q.split() if len(w) > 4 and w in pl)
        if sc > best_sc:
            best_sc = sc
            best = row
    if best is None or best_sc < 4:
        return None
    lines = [
        "## Program scope",
        "Borderplex training program outline derived only from supplied skill-demand inputs.",
        "## Modules",
    ]
    for tok in (best.get("must_include") or [])[:10]:
        title = str(tok).replace("_", " ").strip()
        lines.append(f"### {title}")
        lines.append(
            f"Instructional emphasis aligned with **{title}** using cited posting and skill-frequency signals."
        )
    lines.extend(
        [
            "## Evidence",
            "skill_demand_weekly, extracted_intelligence, and scoped job_postings per inputs.",
            "## Recommended follow-up",
            "Confirm sequencing with regional employers after the next analytics refresh.",
        ]
    )
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Schema mapping: ground truth → pipeline Pydantic models
# ---------------------------------------------------------------------------


def _map_skills_for_llm(gt: dict) -> list[dict]:
    """Map GT skill records to _LLMSkill format."""
    return [
        {
            "skill_name": s.get("skill_name", ""),
            "type": s.get("type", "Technical"),
            "confidence": s.get("confidence", 0.8),
            "required_flag": s.get("required_flag"),
            "source_span": s.get("source_span", {}),
        }
        for s in gt.get("skills", [])
    ]


def _map_tasks_for_llm(gt: dict) -> list[dict]:
    """Map GT task records to TaskRecord format (task_category, seniority_signal)."""
    return [
        {
            "task_description": t.get("task_description", ""),
            "task_category": t.get("category", "core"),
            "seniority_signal": "any",
            "confidence": t.get("confidence", 0.8),
            "source_span": t.get("source_span", {}),
        }
        for t in gt.get("tasks", [])
    ]


def _map_responsibilities_for_llm(gt: dict) -> list[dict]:
    """Map GT responsibility records to ResponsibilityRecord format."""
    return [
        {
            "responsibility_description": r.get("responsibility_description", ""),
            "scope": r.get("scope", "individual"),
            "requires_ai_competency": False,
            "confidence": r.get("confidence", 0.8),
            "source_span": r.get("source_span", {}),
        }
        for r in gt.get("labeled_responsibilities", [])
    ]


def _simulate_metrics(prompt: str, content: str) -> dict[str, Any]:
    """Simulate realistic token counts, cost, and latency."""
    input_tokens = max(1, len(prompt) // 4)
    output_tokens = max(1, len(content) // 4)
    cost_usd = (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000
    latency_ms = random.randint(200, 800)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens_used": input_tokens + output_tokens,
        "cost_usd": round(cost_usd, 6),
        "latency_ms": latency_ms,
    }


# ---------------------------------------------------------------------------
# Mock entry points
# ---------------------------------------------------------------------------


def mock_complete(prompt: str, agent_name: str, **kwargs: Any) -> dict[str, Any]:
    """Mock replacement for llm_adapter.complete().

    Returns a JSON string as content, simulating what the real LLM returns.
    """
    an = (agent_name or "").strip().lower()

    if an == "analytics-intent-classification":
        from analytics.query_engine.intent import intent_heuristic_classification

        q = _extract_intent_user_question(prompt)
        h = intent_heuristic_classification(q) if q else None
        if h:
            payload = {
                "intent": h["intent"],
                "confidence": h["confidence"],
                "extracted_entities": h["extracted_entities"],
            }
            content = json.dumps(payload)
        else:
            content = json.dumps(
                {
                    "intent": "other",
                    "confidence": 0.12,
                    "extracted_entities": {
                        "geographic_terms": [],
                        "role_names": [],
                        "skill_names": [],
                        "time_references": [],
                    },
                }
            )
        metrics = _simulate_metrics(prompt, content)
        time.sleep(metrics["latency_ms"] / 1000.0)
        return {
            "content": content,
            "input_tokens": metrics["input_tokens"],
            "output_tokens": metrics["output_tokens"],
            "cost_usd": metrics["cost_usd"],
            "model_tier": "sonnet",
            "success": True,
            "extraction_failed": False,
        }

    if an == "analytics-qna-synthesis":
        ans = _mock_analytics_synthesis_answer(prompt)
        if not ans:
            gt = _next_gt_record()
            content = json.dumps({"skills": _map_skills_for_llm(gt)})
        else:
            content = ans
        metrics = _simulate_metrics(prompt, content)
        time.sleep(metrics["latency_ms"] / 1000.0)
        return {
            "content": content,
            "input_tokens": metrics["input_tokens"],
            "output_tokens": metrics["output_tokens"],
            "cost_usd": metrics["cost_usd"],
            "model_tier": "sonnet",
            "success": True,
            "extraction_failed": False,
        }

    if an == "analytics-qna-followup":
        content = (
            '["Which employers show the strongest hiring signal?", '
            '"How did weekly posting volumes change most recently?"]'
        )
        metrics = _simulate_metrics(prompt, content)
        time.sleep(metrics["latency_ms"] / 1000.0)
        return {
            "content": content,
            "input_tokens": metrics["input_tokens"],
            "output_tokens": metrics["output_tokens"],
            "cost_usd": metrics["cost_usd"],
            "model_tier": "sonnet",
            "success": True,
            "extraction_failed": False,
        }

    if an == "analytics-curriculum-synthesis":
        c = _mock_curriculum_synthesis_answer(prompt)
        if not c:
            c = (
                "## Program scope\nBorderplex placeholder curriculum.\n\n"
                "## Modules\n### Skills module\nUse inputs only.\n"
            )
        metrics = _simulate_metrics(prompt, c)
        time.sleep(metrics["latency_ms"] / 1000.0)
        return {
            "content": c,
            "input_tokens": metrics["input_tokens"],
            "output_tokens": metrics["output_tokens"],
            "cost_usd": metrics["cost_usd"],
            "model_tier": "sonnet",
            "success": True,
            "extraction_failed": False,
        }

    gt = _next_gt_record()

    # Build response based on agent_name
    if "spam" in agent_name.lower():
        content = json.dumps({"is_spam": False, "confidence": 0.95, "reason": "Legitimate job posting"})
    else:
        content = json.dumps({"skills": _map_skills_for_llm(gt)})

    metrics = _simulate_metrics(prompt, content)
    time.sleep(metrics["latency_ms"] / 1000.0)

    return {
        "content": content,
        "input_tokens": metrics["input_tokens"],
        "output_tokens": metrics["output_tokens"],
        "cost_usd": metrics["cost_usd"],
        "model_tier": "sonnet",
        "success": True,
        "extraction_failed": False,
    }


def mock_invoke_skills_llm(prompt: str, *, agent_name: str | None = None) -> tuple[str, dict[str, Any]]:
    """Mock replacement for llm_client.invoke_skills_llm().

    Handles skills extraction (default) and SOC classification
    (when called via enrichment-agent for SOC prompts).
    """
    gt = _next_gt_record()

    # SOC classification: enrichment-agent uses invoke_skills_llm for SOC
    if agent_name and "enrichment" in agent_name.lower() and "soc" in prompt.lower():
        content = "15-1252"  # Software Developers — realistic mock SOC code
    else:
        content = json.dumps({"skills": _map_skills_for_llm(gt)})

    metrics = _simulate_metrics(prompt, content)
    time.sleep(metrics["latency_ms"] / 1000.0)

    return content, {
        "tokens_used": metrics["tokens_used"],
        "cost_usd": metrics["cost_usd"],
        "latency_ms": metrics["latency_ms"],
        "success": True,
        "extraction_failed": False,
        "error_reason": None,
        "provider": "mock",
        "model": "mock-sonnet-v1",
    }


def mock_invoke_structured(
    prompt: str,
    output_schema: type[TSchema],
    *,
    agent_name: str,
    **kwargs: Any,
) -> tuple[TSchema | None, dict[str, Any]]:
    """Mock replacement for llm_client.invoke_structured_extraction_llm().

    Returns (parsed_model_instance, metadata).
    """
    gt = _next_gt_record()

    # Build structured response matching the Pydantic schema
    schema_name = output_schema.__name__.lower() if hasattr(output_schema, "__name__") else ""

    if "naics" in agent_name.lower() or "naics" in schema_name:
        raw_data = {"naics_code": "541511"}  # Custom Computer Programming Services
    elif "employer" in agent_name.lower() or "employer" in schema_name:
        raw_data = {
            "company_size": "mid_market",
            "ai_maturity_signal": "ai_adopting",
            "sector": "technology",
        }
    elif "tasks" in agent_name.lower():
        raw_data = {"tasks": _map_tasks_for_llm(gt)}
    elif "responsibilities" in agent_name.lower() or "resp" in agent_name.lower():
        raw_data = {"responsibilities": _map_responsibilities_for_llm(gt)}
    else:
        raw_data = {"skills": _map_skills_for_llm(gt)}

    content_str = json.dumps(raw_data)
    metrics = _simulate_metrics(prompt, content_str)
    time.sleep(metrics["latency_ms"] / 1000.0)

    try:
        parsed = output_schema.model_validate(raw_data)
    except Exception:
        parsed = None

    return parsed, {
        "tokens_used": metrics["tokens_used"],
        "cost_usd": metrics["cost_usd"],
        "latency_ms": metrics["latency_ms"],
        "success": parsed is not None,
        "extraction_failed": parsed is None,
        "error_reason": None if parsed is not None else "mock_schema_validation_failed",
        "provider": "mock",
        "model": "mock-sonnet-v1",
    }
