"""Workforce Intelligence Q&A — intent classification (Analytics / Week 8).

Classifies natural-language questions into ARCHITECTURE_DEEP.md intent buckets
and extracts lightweight entities for routing and SQL generation.

Uses :func:`common.llm_adapter.complete` with a Haiku-tier model by default for
latency and cost. Provider selection follows ``LLM_PROVIDER`` (anthropic, mock,
gemini).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Final

import structlog
from pydantic import BaseModel, Field, field_validator

from common.llm_adapter import complete

log = structlog.get_logger()

_AGENT_NAME = "analytics-intent-classification"

# Default: Haiku-class (see common.llm_adapter.MODEL_TIER_MAP).
_DEFAULT_INTENT_MODEL = "claude-haiku-4-5"

INTENT_CATEGORIES: Final[tuple[str, ...]] = (
    "trend",
    "role_evolution",
    "disruption",
    "emergence",
    "curriculum",
    "employer",
    "workflow",
    "geographic",
    "comparison",
    "other",
)

_INTENT_SET = frozenset(INTENT_CATEGORIES)

_SYSTEM_PROMPT = """You are an intent classifier for workforce and labor-market analytics questions.

Choose exactly ONE primary intent from this closed list (snake_case):
- trend: demand, growth, velocity, time series, "how fast", "going up or down"
- role_evolution: how a job/role is changing, titles, responsibilities over time
- disruption: automation, AI impact, displacement, restructuring, risk to occupations
- emergence: new roles, emerging skills, novel job families, "jobs that didn't exist"
- curriculum: training, credentials, learning paths, bootcamps, upskilling programs
- employer: hiring practices, benefits, employer demand, company-specific hiring
- workflow: day-to-day tasks, tools used on the job, process, "what does a X do daily"
- geographic: regions, cities, borderplex, remote vs on-site location, "where"
- comparison: comparing A vs B, two skills, two regions, two time periods, rankings
- other: meta, unclear, chit-chat, or none of the above fit

Also extract entities mentioned in the question (use empty lists if none):
- geographic_terms: place names, regions (e.g. El Paso, Texas, remote US)
- role_names: job titles or occupation names
- skill_names: technologies or skills (e.g. Python, welding)
- time_references: explicit periods (e.g. last 90 days, 2024, Q1, past year)

For ambiguous or multi-topic questions, pick the single best primary intent and
set confidence lower (e.g. 0.45–0.65). If nothing fits, use intent "other" with
low confidence.

Respond with JSON ONLY, no markdown, matching this shape:
{"intent":"<one of the list>","confidence":0.0,"extracted_entities":{"geographic_terms":[],"role_names":[],"skill_names":[],"time_references":[]}}
"""


class ExtractedEntities(BaseModel):
    """Entity lists parsed from the user question (all optional, default empty)."""

    geographic_terms: list[str] = Field(default_factory=list)
    role_names: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    time_references: list[str] = Field(default_factory=list)

    @field_validator(
        "geographic_terms",
        "role_names",
        "skill_names",
        "time_references",
        mode="before",
    )
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            s = v.strip()
            return [s] if s else []
        if isinstance(v, list):
            out: list[str] = []
            for x in v:
                if x is None:
                    continue
                if isinstance(x, str) and (t := x.strip()):
                    out.append(t)
                elif isinstance(x, (int, float)):
                    out.append(str(x))
            return out
        return []


class IntentClassification(BaseModel):
    """Structured LLM output for intent routing."""

    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_entities: ExtractedEntities = Field(default_factory=ExtractedEntities)

    @field_validator("intent", mode="before")
    @classmethod
    def _normalize_intent(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "other"
        s = str(v).strip().lower().replace(" ", "_").replace("-", "_")
        if s in _INTENT_SET:
            return s
        # common aliases
        aliases = {
            "geo": "geographic",
            "location": "geographic",
            "regional": "geographic",
            "compare": "comparison",
            "versus": "comparison",
            "vs": "comparison",
            "trends": "trend",
            "hiring": "employer",
            "education": "curriculum",
            "training": "curriculum",
        }
        return aliases.get(s, "other")


def _intent_model() -> str:
    return os.getenv("INTENT_CLASSIFICATION_MODEL", _DEFAULT_INTENT_MODEL)


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _parse_llm_json(content: str) -> dict[str, Any] | None:
    raw = _strip_json_fence(content)
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _fallback_other(reason: str) -> dict[str, Any]:
    log.info("intent_classification_fallback", reason=reason)
    return {
        "intent": "other",
        "confidence": 0.0,
        "extracted_entities": {
            "geographic_terms": [],
            "role_names": [],
            "skill_names": [],
            "time_references": [],
        },
    }


def classify_workforce_question(
    question: str,
    *,
    correlation_id: str | None = None,
    max_tokens: int = 500,
) -> dict[str, Any]:
    """Classify a free-text workforce question and extract entities.

    Returns a plain dict: ``{"intent": str, "confidence": float,
    "extracted_entities": dict}`` suitable for JSON APIs. On LLM failure or
    invalid JSON, returns ``intent="other"``, ``confidence=0.0``, and empty entity
    lists.

    Uses :func:`common.llm_adapter.complete` with the model from
    ``INTENT_CLASSIFICATION_MODEL`` (default Haiku-class).
    """
    q = (question or "").strip()
    if not q:
        return _fallback_other("empty_question")

    prompt = f"User question:\n{q}\n"
    model = _intent_model()

    try:
        result = complete(
            prompt=prompt,
            agent_name=_AGENT_NAME,
            model=model,
            system=_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        log.warning("intent_classification_llm_exception", error_type=type(exc).__name__)
        return _fallback_other("llm_exception")

    if not result.get("success") or result.get("extraction_failed"):
        return _fallback_other("llm_failed")

    content = (result.get("content") or "").strip()
    parsed = _parse_llm_json(content)
    if not parsed:
        return _fallback_other("invalid_json")

    try:
        validated = IntentClassification.model_validate(parsed)
    except Exception:
        return _fallback_other("schema_validation")

    out_entities = validated.extracted_entities.model_dump()
    return {
        "intent": validated.intent,
        "confidence": float(validated.confidence),
        "extracted_entities": out_entities,
    }
