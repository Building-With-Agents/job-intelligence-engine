"""Workforce Intelligence Q&A — intent classification (Analytics / Week 8).

Classifies natural-language questions into ARCHITECTURE_DEEP.md intent buckets
and extracts lightweight entities for routing and SQL generation.

Uses :func:`common.llm_adapter.complete` with ``role="classification"`` so the
call routes through the provider-agnostic adapter (Azure OpenAI ``chat-gpt41mini``
via ``LLM_DEFAULT`` by default — see ``.cursor/rules/llm-routing.mdc``).

JIE #258 — per-stage Langfuse observations: ``classify_workforce_question`` is
wrapped with ``@observe(as_type="generation")`` so each intent-classification call
appears as its own Langfuse generation nested inside the parent Q&A trace, enabling
per-stage cost attribution and latency breakdown.
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any, Final

import structlog
from pydantic import BaseModel, Field, field_validator

from analytics.query_engine.langfuse_utils import lf_context as langfuse_context
from analytics.query_engine.langfuse_utils import lf_observe as _lf_observe
from analytics.query_engine.langfuse_utils import report_langfuse_usage
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

# Pair D / Week 10 — staged deterministic shortcuts (eval replay via QA_EVAL_INTENT_HEURISTIC_LEVEL).
_CURRICULUM_TRAINING_PROGRAM_COVER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bwhat\s+should\b.{0,140}?\btraining\s+program\s+cover\b",
    re.IGNORECASE,
)
_WORKFLOW_DATA_PIPELINE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bfor\s+data\s+engineering\s+roles\b.{0,260}?\b(?:data\s+pipeline|orchestration|workflow\s+tools)\b",
    re.IGNORECASE,
)
_BORDERPLEX_EMPLOYERS_RANKED_SHARE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bwhich\s+borderplex\s+employers\b.{0,260}?\b(?:share|highest)\b",
    re.IGNORECASE,
)


def _intent_heuristic_ablation_level() -> int:
    """Higher enables more Pair D heuristics. Unset → all off (level 0). Set 1-3 for staged eval baselines."""

    raw = os.getenv("QA_EVAL_INTENT_HEURISTIC_LEVEL")
    if raw is None or not str(raw).strip():
        return 0
    try:
        return max(0, min(3, int(str(raw).strip())))
    except ValueError:
        return 0


def _empty_extracted_entities() -> dict[str, list[str]]:
    return {
        "geographic_terms": [],
        "role_names": [],
        "skill_names": [],
        "time_references": [],
    }


def _heuristic_classification_dict(*, intent: str, confidence: float, reason: str) -> dict[str, Any]:
    log.info("intent_classification_heuristic", intent=intent, reason=reason, confidence=confidence)
    conf = float(confidence)
    return {
        "intent": intent,
        "confidence": conf,
        "needs_clarification": conf < _CLARIFICATION_THRESHOLD,
        "extracted_entities": _empty_extracted_entities(),
    }


def intent_heuristic_classification(question: str) -> dict[str, Any] | None:
    """Deterministic intent shortcuts before the classifier LLM (mock-friendly).

    ``QA_EVAL_INTENT_HEURISTIC_LEVEL`` gates which tier runs (0–3) so Pair D can
    replay before/after harness scores without reverting code.
    """

    q = (question or "").strip()
    if not q:
        return None
    tier = _intent_heuristic_ablation_level()

    if _matches_curriculum_generation_shape(q):
        return _heuristic_classification_dict(
            intent="curriculum",
            confidence=_CURRICULUM_HEURISTIC_CONFIDENCE,
            reason="curriculum_generation_shape",
        )

    if tier >= 1 and _CURRICULUM_TRAINING_PROGRAM_COVER_PATTERN.search(q):
        return _heuristic_classification_dict(
            intent="curriculum",
            confidence=_CURRICULUM_HEURISTIC_CONFIDENCE,
            reason="curriculum_training_program_cover",
        )

    if tier >= 2 and _WORKFLOW_DATA_PIPELINE_PATTERN.search(q):
        return _heuristic_classification_dict(intent="workflow", confidence=0.90, reason="workflow_data_pipeline")

    if tier >= 3 and _BORDERPLEX_EMPLOYERS_RANKED_SHARE_PATTERN.search(q):
        return _heuristic_classification_dict(intent="employer", confidence=0.91, reason="borderplex_employers_share")

    return None


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
- comparison: comparing A vs B, two skills, two regions, two time periods, rankings.
  TIE-BREAKER: comparing two EMPLOYER cohorts within ONE region (e.g. academic vs
  private-sector, federal contractors vs commercial, public vs private) is primary
  intent EMPLOYER, not comparison. Reserve comparison for two LOCATIONS, two TIME
  PERIODS, or two SKILLS / ROLES.
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

DISRUPTION vs other intents — TIE-BREAKER (when automation / AI / era-shift signals appear):
  → disruption: Primary ask is structural labor-market change tied to AI, automation,
     displacement, augmentation, transformation, or era-defined skill/tool mix turnover
     (e.g. pre_chatgpt vs post_gpt4, early_genai vs agentic_era). Includes demand decline
     paired with automation/RPA/AI-tool signals, or "how skill composition shifted between
     temporal buckets" for a role family.
  → role_evolution: How duties, titles, or responsibilities of a role evolve narratively,
     without the question centering on automation risk, era-bucket fingerprints, or
     mix turnover across locked temporal periods — use role_evolution only when that
     softer "how the job is changing" framing dominates.
  → comparison: Two symmetric entities (two skills, two regions, two employers) ranked
     or contrasted on equal footing. Era labels used to measure structural mix or AI
     intensity change (not a balanced A-vs-B leaderboard of arbitrary peers) → disruption,
     not comparison.
  → trend: Demand velocity, growth, or time series "up or down" without era-bucket or
     transformation/displacement framing. Pure posting-volume or posting-count questions
     framed as quarter-over-quarter, month-over-month, or week-over-week — with NO AI /
     automation / displacement / skill-or-tool-mix framing — stay in trend, even when era
     tokens (pre_chatgpt, early_genai, post_gpt4, agentic_era) appear only as a time-axis
     descriptor for the comparison range (e.g. "across the pre_chatgpt → agentic_era
     periods"). The era tokens describe the WHEN, not the WHAT, in this case. Share or
     distribution questions whose SUBJECT is non-AI — remote-eligibility share, salary
     distribution, experience-bar requirements, headcount, or other workforce attributes
     unrelated to AI / automation / skill-or-tool-mix turnover — stay in trend even when
     framed across era buckets (e.g. "between pre_chatgpt and agentic_era"); the era
     tokens scope the comparison range, they do not by themselves promote a non-AI
     subject to disruption. If temporal eras or AI/automation restructuring is central
     to the WHAT → disruption, not trend.
  → geographic: Location filters which postings are in scope, but the analytic axis is
     still era/skill/automation shift → disruption. Use geographic only when listing or
     filtering postings by place is the main task; a Borderplex (or similar) filter alone
     does not override disruption when the core question is mix or automation change across eras.
  ROLE_EVOLUTION CARVE-OUT (AI-supporting-mix / AI-expected-bar within an existing role family):
     PREREQUISITE — this carve-out ONLY applies when the question contains explicit AI
     framing: a named AI-assist tool (Copilot, ChatGPT, LLM tooling, AI-assisted dev
     tools, etc.) OR explicit AI-literacy / AI-familiarity / AI-readiness language. A
     question about generic skill-mix evolution, experience-bar change, or seniority
     requirements with NO mention of AI, AI tools, or AI-literacy does NOT qualify
     for this carve-out — route those questions by the standard rules (trend, disruption,
     or role_evolution on their own merits), regardless of era buckets present.
     When the PREREQUISITE is met, route to role_evolution — even when era buckets
     (pre_chatgpt, early_genai, post_gpt4, agentic_era, "the four temporal periods")
     are present — when the question asks how an existing role family's *AI-supporting-
     skill mix*, *expected AI-literacy level / bar / familiarity*, or *balance* between
     AI-assistant skills and traditional / fundamental skills has evolved / changed /
     shifted across those periods, provided EITHER:
       (a) an AI-assist tool or concept is EXPLICITLY NAMED in the question text as one
           peer in a broader multi-skill list (e.g. "TypeScript, Next.js, testing
           frameworks, AI-assisted dev tools" for React; "AI-assistant familiarity and
           traditional programming skills" for software engineers). A generic "mix of
           required skills evolved" or "which skills are rising / declining" question
           with NO named AI-assist concept does NOT qualify — route to trend or
           role_evolution on their own merits, OR
       (b) the *expected AI-literacy bar / AI-familiarity level / AI-readiness
           expectation* within an existing role is the explicit subject (e.g. "expected
           AI-literacy bar for product managers", "how has the AI-familiarity expectation
           evolved"). A generic experience bar, seniority level, or years-of-experience
           requirement WITHOUT explicit AI framing does NOT qualify for this sub-condition.
     DISAMBIGUATOR vs disruption: AI-adoption SHARE / PERCENTAGE / GROWTH RATE / VELOCITY
     / PENETRATION as the PRIMARY subject ("what share of postings mention Copilot",
     "how fast is that share growing") → disruption (per the SHARE / GROWTH / VELOCITY
     of AI-ADOPTION rule below). "Balance / AI-supporting-mix / AI-expected-bar evolution"
     with AI-assist as peer or expectation → role_evolution. Skill-composition SHIFT,
     skills DROPPED OUT, role TRANSFORMED, AI-DENSITY THRESHOLD CROSSING, displacement
     by automation → disruption (per the disruption bullet above), not role_evolution.

EMERGENCE vs DISRUPTION — TIE-BREAKER (when era buckets like pre_chatgpt / agentic_era appear):
  → emergence: The subject is something NEW appearing for the first time — net-new roles,
     novel job families, AI-native titles, newly required tools/credentials/certifications
     that did NOT exist in the prior era. The question asks "which NEW X appeared?",
     "what is first-seen / newly emerging / didn't exist before?", or detects roles that
     were absent (or near-zero) in pre_chatgpt and now appear at meaningful volume in the
     agentic_era. Threshold-style framing ("<5 postings before ChatGPT, 50+ now") is
     emergence, not disruption.
  → disruption: The subject is an EXISTING role whose skill mix, tasks, or composition
     was TRANSFORMED across eras. The role existed before AND after; the question is how
     it changed (skills dropped out, AI-adjacent skills entered, automation reshaped duties).
  RULE: era references alone do not decide between these. Ask: "Is the subject something
     that DIDN'T EXIST PREVIOUSLY and is now appearing (emergence), or something that
     EXISTED PREVIOUSLY and was transformed (disruption)?" New tools/credentials/titles
     framed as "first-seen" or "did not exist in pre_chatgpt" → emergence.
  PRECEDENCE: when a question contains EXPLICIT first-seen language — "did not exist
     before", "did not exist in [prior era]", "first appeared", "first-seen", "newly
     emerging", "tools that did not exist in the pre_chatgpt period" — emergence takes
     precedence over BOTH the disruption tie-breaker above AND the SHARE / GROWTH /
     VELOCITY of AI-ADOPTION rule below, even when era buckets are present and the
     subject is AI tools (LangChain, LangGraph, vector databases, LLM orchestration
     frameworks, etc.). The first-seen cue ("tools that did not exist in [prior era]")
     beats the share-of-AI-adoption cue ("share of postings that mention AI tools")
     when both appear in the same question, because the question is asking about NEW
     tools relative to a prior-era baseline — i.e. emergence — not about the structural
     penetration of existing-but-growing AI tools in a role family.

SHARE / GROWTH / VELOCITY of AI-ADOPTION — TIE-BREAKER (disruption vs trend):
  When the question asks about a SHARE, GROWTH RATE, VELOCITY, or "how fast X is growing"
  AND the SUBJECT of that share/growth is one of:
    - AI-tool / AI-assistant adoption inside a role family (Copilot, ChatGPT, Cursor,
      AIOps, LLM-driven incident triage, Copilot-for-infra-as-code, AI-assisted testing)
    - automation / RPA penetration in an existing role
    - displacement or augmentation of humans by AI in tasks
    - transformation of an existing role's skill or task mix
    - turnover of the skill / tool mix used by a role family
  → disruption. The share/velocity here MEASURES structural AI/automation penetration,
     not generic posting demand. Era buckets are NOT required — "current-period share"
     plus "how fast is that share growing" is sufficient when the subject is AI adoption.
  → trend remains the default for share / growth / velocity questions whose subject is
     GENERIC posting or skill demand WITHOUT AI/automation/displacement/transformation
     framing (e.g. "how fast is Python demand growing in El Paso?" → trend; "what share
     of cybersecurity postings are remote and how fast is that growing?" → trend).

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

Q: "Show open AI-core roles (AI agent developer, prompt engineer, ML engineer) at UTEP, NMSU, or EPCC in the last 12 months."
A: {"intent":"employer","confidence":0.91,...}
   ← EMPLOYERS (named institutions) are the subject; "at <institutions>" is the
     primary filter. No city/region token scopes the postings — the institutions
     ARE the scope. Distinct from "Las Cruces postings, highlighting NMSU/UTEP/EPCC"
     where the city is the filter and institutions are secondary qualifiers.

Q: "List all IT postings from Borderplex federal-contractor employers requiring a security clearance."
A: {"intent":"employer","confidence":0.90,...}
   ← EMPLOYERS (federal-contractor cohort, defined by sector / employer_profiles)
     are the subject. "Borderplex" scopes WHICH employers are included; it is not
     a posting filter. Same pattern: "Borderplex fintech / payments employers"
     and "Borderplex healthcare-IT employers" → employer.

Q: "Compare AI engineering hiring in El Paso vs Las Cruces over the last 6 months."
A: {"intent":"comparison","confidence":0.88,...}
   ← Two locations being compared side-by-side.

Q: "What should a training program for a cybersecurity analyst in El Paso look like in the next 2 years?"
A: {"intent":"curriculum","confidence":0.91,...}
   ← Program/curriculum design for a role; the city is context, not a posting filter.

Q: "Design a curriculum for entry-level data engineers in the Borderplex that emphasizes GenAI toolchains."
A: {"intent":"curriculum","confidence":0.9,...}
   ← Curriculum design. Not the same as "show all Borderplex postings" (geographic).

Q: "For Borderplex IT roles, how did skill composition shift between pre_chatgpt and post_gpt4?"
A: {"intent":"disruption","confidence":0.91,...}
   ← Era-pair skill-mix / structural turnover; automation-era framing → disruption.

Q: "In Borderplex software engineering postings, how does AI-assistant tool adoption today compare to one year ago?"
A: {"intent":"disruption","confidence":0.89,...}
   ← Adoption shift of AI workplace tools over time → disruption, not a simple demand trend.

Q: "Which Borderplex IT roles had fewer than five postings before ChatGPT but have grown to 50+ postings in the agentic_era period, and what skills are driving that growth?"
A: {"intent":"emergence","confidence":0.88,...}
   ← Roles essentially did not exist (<5 postings) in the pre_chatgpt era and now appear
      at meaningful volume → NEW roles appearing → emergence. Era buckets here measure
      first-appearance / growth-from-near-zero, not transformation of an existing role.

Q: "For Borderplex DevOps and SRE postings, what share of current-period postings reference AI-assisted operations (AIOps, LLM-driven incident triage, Copilot for infra-as-code), and how fast is that share growing?"
A: {"intent":"disruption","confidence":0.90,...}
   ← Share + growth-rate of AI-assistant adoption inside an existing role family
      (DevOps / SRE) measures structural AI/automation penetration, not generic posting
      demand → disruption, not trend. No era bucket required when the subject is AI adoption.

Q: "How have data analyst job descriptions in the Borderplex shifted toward analytics engineering or cloud tooling in the past year?"
A: {"intent":"role_evolution","confidence":0.90,...}
   ← role_evolution: qualitative shift in titles/responsibilities/skill mix for a role family;
   not the same as trend (weekly demand counts / velocity alone).

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


@_lf_observe(as_type="generation", name="intent_classification")
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

    heuristic = intent_heuristic_classification(q)
    if heuristic is not None:
        return heuristic

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

    langfuse_context.update_current_observation(
        input=q,
        metadata={"agent_name": _AGENT_NAME, "role": "classification"},
    )

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
        langfuse_context.update_current_observation(level="ERROR", status_message=str(exc))
        return _fallback_other("llm_exception")

    report_langfuse_usage(result)

    if not result.get("success") or result.get("extraction_failed"):
        langfuse_context.update_current_observation(level="WARNING", status_message="llm_failed")
        return _fallback_other("llm_failed")

    content = (result.get("content") or "").strip()
    parsed = _parse_llm_json(content)
    if not parsed:
        langfuse_context.update_current_observation(level="WARNING", status_message="invalid_json")
        return _fallback_other("invalid_json")

    try:
        validated = IntentClassification.model_validate(parsed)
    except Exception:
        langfuse_context.update_current_observation(level="WARNING", status_message="schema_validation")
        return _fallback_other("schema_validation")

    out_entities = validated.extracted_entities.model_dump()
    needs_clarification = validated.confidence < _CLARIFICATION_THRESHOLD
    classification_out = {
        "intent": validated.intent,
        "confidence": float(validated.confidence),
        "needs_clarification": needs_clarification,
        "extracted_entities": out_entities,
    }
    langfuse_context.update_current_observation(
        output={"intent": validated.intent, "confidence": float(validated.confidence)},
        metadata={"needs_clarification": needs_clarification},
    )
    return classification_out
