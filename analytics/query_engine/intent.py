"""Workforce Intelligence Q&A — intent classification (Analytics / Week 8).

Classifies natural-language questions into ARCHITECTURE_DEEP.md intent buckets
and extracts lightweight entities for routing and SQL generation.

Uses :func:`common.llm_adapter.complete` with ``role="classification"`` so the
call routes through the provider-agnostic adapter (Azure OpenAI ``chat-gpt41mini``
via ``LLM_DEFAULT`` by default — see ``.cursor/rules/llm-routing.mdc``).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Final

import structlog
from pydantic import BaseModel, Field, field_validator

from common.llm_adapter import complete

if TYPE_CHECKING:
    from analytics.query_engine.schemas import CostLedger

log = structlog.get_logger()

_AGENT_NAME = "analytics-intent-classification"
_CLARIFICATION_THRESHOLD = 0.55

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

# Heuristic: curriculum *generation* (program/learning design for a role). Runs before the LLM so
# stable phrasing routes to "curriculum" even when a location or employer is mentioned in passing.
# Keep patterns tight to avoid misrouting disruption/trend/geography primary intents.
_CURRICULUM_GENERATION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\bwhat should (?:a |an |the )?training program for\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhow should (?:we |I )?structure (?:a |an |the )?training (?:program|curriculum) for\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdesign (?:a |an |the )?curriculum for\b", re.IGNORECASE),
    re.compile(
        r"\bwhat skills should (?:we |I |my (?:org|team|program) )?teach (?:for|to|in)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:recommend|suggest|propose|outline|map|draft|plan|sketch)\b"
        r".{0,100}?\b(?:a |an |the )?"
        r"(?:syllabus|workforce (?:curriculum|program)|"
        r"(?:certificate|vocational|undergraduate|graduate) (?:curriculum|program)|"
        r"learning (?:path|journey|track|roadmap|plan)|"
        r"bootcamp (?:curriculum|program|outline)|"
        r"training (?:path|roadmap|curriculum|program|track|outline))\s+for\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:build|create|develop|architect|shape)\b"
        r".{0,40}?\b(?:a |an |the )?(?:curriculum|learning path|syllabus|"
        r"training (?:path|roadmap|program|outline))\s+for\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bprogram design (?:and delivery )?for\b",
        re.IGNORECASE,
    ),
)


def _matches_curriculum_generation_shape(question: str) -> bool:
    q = (question or "").strip()
    if not q or len(q) < 20:
        return False
    return any(pat.search(q) for pat in _CURRICULUM_GENERATION_PATTERNS)


_CURRICULUM_HEURISTIC_CONFIDENCE = 0.92

_SYSTEM_PROMPT = """You are an intent classifier for workforce and labor-market analytics questions.

Choose exactly ONE primary intent from this closed list (snake_case):
- trend: demand, growth, velocity, time series, "how fast", "going up or down"
- role_evolution: how a job/role is changing, titles, responsibilities over time
- disruption: automation, AI impact, displacement, restructuring, risk to occupations
- emergence: new roles, emerging skills, novel job families, "jobs that didn't exist"
- curriculum: training, credentials, learning paths, bootcamps, upskilling programs, **and
  program design for a target role** — e.g. "What should a training program for [role] look
  like?", "Design a curriculum for [role]…", "What skills should we teach for [role]?",
  "Outline a learning path for …". If the user is asking *how to design* or *what to include
  in* education for a profession (not listing postings or ranking employers by location), use
  curriculum even when a city/region is mentioned as context.
- employer: EMPLOYERS are the grammatical subject — ranked, compared, or analyzed by
  hiring behavior, role share, AI adoption, turnover, or other employer-level attributes.
  A region token that scopes which employers are included (e.g. "Borderplex employers",
  "El Paso companies") does NOT make this geographic; the subject is still employers.
- workflow: day-to-day tasks, tools used on the job, process, "what does a X do daily"
- geographic: POSTINGS are the grammatical subject, filtered by a location — a city, region,
  state, or the Borderplex (El Paso, Las Cruces, Ciudad Juárez, Doña Ana). Use this whenever
  the question lists, retrieves, shows, or filters postings *in* or *for* a specific place,
  even when roles, domain keywords (AI/ML, fintech, healthcare-IT, cybersecurity), or
  employer/institution names appear as secondary qualifiers alongside the location.
  RULE: explicit city/region token present AND postings are filtered by that place → geographic.
- comparison: comparing A vs B, two skills, two regions, two time periods, rankings
- other: meta, unclear, chit-chat, or none of the above fit

GEOGRAPHIC vs EMPLOYER — TIE-BREAKER (apply whenever both signals are present):
  → Ask: what is the grammatical subject — POSTINGS or EMPLOYERS?
  → POSTINGS filtered by a location → geographic. The presence of role titles, domain
     keywords (AI/ML, fintech, DevOps), or named employers/institutions (NMSU, UTEP, EPCC)
     does NOT override a city or region as the primary axis.
  → EMPLOYERS ranked or analyzed within a market → employer. A region token that scopes
     the employer set (e.g. "Borderplex employers", "El Paso companies") does NOT flip this
     to geographic. Ask: "Is the region a filter on POSTINGS or a scope for EMPLOYERS?"
     If it scopes employers → employer.

Anchoring examples (few-shot):
Q: "Show all El Paso, TX postings for AI agent developer, prompt engineer, or LLM engineer roles."
A: {"intent":"geographic","confidence":0.95,...}
   ← POSTINGS are the subject; El Paso filters them. Role names are secondary qualifiers.

Q: "List all Las Cruces, NM AI/ML researcher postings, highlighting employers such as NMSU or UTEP."
A: {"intent":"geographic","confidence":0.92,...}
   ← POSTINGS are the subject; Las Cruces filters them. Institution names are secondary.

Q: "Show all cybersecurity postings in the Borderplex from the last 12 months."
A: {"intent":"geographic","confidence":0.90,...}
   ← POSTINGS are the subject; Borderplex filters them by location.

Q: "Which Borderplex employers have the highest share of postings mentioning AI tools?"
A: {"intent":"employer","confidence":0.91,...}
   ← EMPLOYERS are ranked; Borderplex scopes the employer set, not a posting filter.

Q: "What is Dell's hiring strategy for data scientists?"
A: {"intent":"employer","confidence":0.93,...}
   ← EMPLOYERS are the subject; no geographic posting filter.

Q: "Compare AI engineering hiring in El Paso vs Las Cruces over the last 6 months."
A: {"intent":"comparison","confidence":0.88,...}
   ← Two locations being compared side-by-side.

Q: "What should a training program for a cybersecurity analyst in El Paso look like in the next 2 years?"
A: {"intent":"curriculum","confidence":0.91,...}
   ← Program/curriculum design for a role; the city is context, not a posting filter.

Q: "Design a curriculum for entry-level data engineers in the Borderplex that emphasizes GenAI toolchains."
A: {"intent":"curriculum","confidence":0.9,...}
   ← Curriculum design. Not the same as "show all Borderplex postings" (geographic).

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
        "needs_clarification": True,
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
    cost_ledger: CostLedger | None = None,
    conversation_context: str | None = None,
) -> dict[str, Any]:
    """Classify a free-text workforce question and extract entities.

    Returns a plain dict: ``{"intent": str, "confidence": float,
    "needs_clarification": bool, "extracted_entities": dict}`` suitable for
    JSON APIs. On LLM failure or
    invalid JSON, returns ``intent="other"``, ``confidence=0.0``, and empty entity
    lists.

    Routes through :func:`common.llm_adapter.complete` with ``role="classification"``
    (Haiku-tier; resolves to Azure OpenAI ``chat-gpt41mini`` via ``LLM_DEFAULT``).
    """
    q = (question or "").strip()
    if not q:
        return _fallback_other("empty_question")

    if _matches_curriculum_generation_shape(q):
        log.info("intent_classification_curriculum_heuristic", match="curriculum_generation_shape")
        return {
            "intent": "curriculum",
            "confidence": float(_CURRICULUM_HEURISTIC_CONFIDENCE),
            "needs_clarification": _CURRICULUM_HEURISTIC_CONFIDENCE < _CLARIFICATION_THRESHOLD,
            "extracted_entities": {
                "geographic_terms": [],
                "role_names": [],
                "skill_names": [],
                "time_references": [],
            },
        }

    ctx = (conversation_context or "").strip()
    if ctx:
        prompt = (
            "The user is continuing a conversation. Use the prior Q&A below to resolve "
            "pronouns, “the same region”, and short follow-up questions that refer to earlier context.\n\n"
            f"{ctx}\n\n"
            f"Current user question (this turn only):\n{q}\n"
        )
    else:
        prompt = f"User question:\n{q}\n"

    try:
        result = complete(
            prompt=prompt,
            agent_name=_AGENT_NAME,
            role="classification",
            system=_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            correlation_id=correlation_id,
        )
        if cost_ledger is not None:
            from analytics.query_engine.ledger_utils import append_leg_from_complete

            append_leg_from_complete(cost_ledger, "intent_classification", result, model_fallback=None)
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
    needs_clarification = validated.confidence < _CLARIFICATION_THRESHOLD
    return {
        "intent": validated.intent,
        "confidence": float(validated.confidence),
        "needs_clarification": needs_clarification,
        "extracted_entities": out_entities,
    }
