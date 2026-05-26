"""Role-family taxonomy: config load, NL resolution, and cluster classification (JIE #362).

Backfill approach: rule-based token/alias scoring on ``label`` (+ optional
``representative_titles`` / ``top_skills``); optional LLM batch for unmapped labels
via ``--use-llm`` in ``scripts/backfill_role_family.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import structlog

from common.config_loader import load_yaml

log = structlog.get_logger()

# Minimum alias match length (chars) to avoid spurious single-token hits.
_MIN_ALIAS_LEN = 4

# Rule classifier: minimum score to assign without LLM.
_MIN_RULE_SCORE = 2

_JOB_COUNT_SIGNAL_RE = re.compile(
    r"\b("
    r"how many|number of|count of|how many job|job postings?|jobs posted|"
    r"roles posted|posting count|compare.*postings?|postings? for .+ roles?"
    r")\b",
    re.IGNORECASE,
)

_SKILL_MENTION_COMPARISON_RE = re.compile(
    r"\bskills?\b.{0,40}\b(vs\.?|versus|compared to|compare)\b|"
    r"\b(vs\.?|versus|compared to|compare)\b.{0,40}\bskills?\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class RoleFamilyEntry:
    slug: str
    aliases: tuple[str, ...]


def load_role_family_config() -> list[RoleFamilyEntry]:
    """Load families from ``config/role_families.yaml`` via ``load_yaml("role_families")``."""
    data = load_yaml("role_families")
    raw = data.get("role_families") or {}
    families = raw.get("families") if isinstance(raw, dict) else None
    if not isinstance(families, list):
        return []
    out: list[RoleFamilyEntry] = []
    for item in families:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        aliases_raw = item.get("aliases") or []
        aliases = tuple(str(a).strip().lower() for a in aliases_raw if str(a).strip())
        out.append(RoleFamilyEntry(slug=slug, aliases=aliases))
    return out


def _normalized_phrases(*phrases: str) -> str:
    parts: list[str] = []
    for p in phrases:
        s = str(p or "").strip().lower()
        if not s:
            continue
        s = re.sub(r"[-_/]+", " ", s)
        parts.append(s)
    return " ".join(parts)


def resolve_role_families_from_text(*phrases: str) -> list[str]:
    """Map free text to unique family slugs (longest alias match wins per span)."""
    haystack = _normalized_phrases(*phrases)
    if not haystack:
        return []

    families = load_role_family_config()
    # (alias_len, slug, alias) — prefer longer aliases first
    candidates: list[tuple[int, str, str]] = []
    for entry in families:
        for alias in entry.aliases:
            if len(alias) >= _MIN_ALIAS_LEN and alias in haystack:
                candidates.append((len(alias), entry.slug, alias))

    candidates.sort(key=lambda t: (-t[0], t[1]))
    matched_slugs: list[str] = []
    seen: set[str] = set()
    used_spans: list[tuple[int, int]] = []

    for _alen, slug, alias in candidates:
        if slug in seen:
            continue
        start = haystack.find(alias)
        if start < 0:
            continue
        end = start + len(alias)
        overlap = any(not (end <= s or start >= e) for s, e in used_spans)
        if overlap:
            continue
        used_spans.append((start, end))
        seen.add(slug)
        matched_slugs.append(slug)

    return matched_slugs


def question_signals_job_posting_count(question: str) -> bool:
    """True when the question asks for job/posting counts (not skill-mention demand)."""
    return bool(_JOB_COUNT_SIGNAL_RE.search(question or ""))


def question_signals_skill_mention_comparison(question: str) -> bool:
    """True when the question frames a skill-vs-skill comparison."""
    return bool(_SKILL_MENTION_COMPARISON_RE.search(question or ""))


def comparison_should_use_role_family_count(
    question: str,
    role_names: list[str],
    skill_names: list[str],
) -> bool:
    """True when comparison should aggregate ``job_postings`` by ``role_family``.

    Requires at least two resolved families and a job-count phrasing signal.
    Single-family resolution falls through to skill_demand_weekly / sector paths.
    """
    if not question_signals_job_posting_count(question):
        return False
    if question_signals_skill_mention_comparison(question) and not question_signals_job_posting_count(question):
        return False
    families = resolve_role_families_from_text(question, *role_names, *skill_names)
    return len(families) >= 2


def comparison_resolved_role_families(
    question: str,
    role_names: list[str],
    skill_names: list[str],
) -> list[str]:
    """Family slugs for the role-family comparison path, or ``[]`` if not applicable."""
    if not comparison_should_use_role_family_count(question, role_names, skill_names):
        return []
    return resolve_role_families_from_text(question, *role_names, *skill_names)


def _collect_classifier_text(
    label: str,
    *,
    representative_titles: list[str] | None,
    top_skills: list | None,
) -> str:
    parts = [label or ""]
    for t in representative_titles or []:
        if isinstance(t, str) and t.strip():
            parts.append(t)
    for sk in top_skills or []:
        if isinstance(sk, dict):
            name = sk.get("skill_name") or sk.get("label") or ""
            if name:
                parts.append(str(name))
        elif isinstance(sk, str):
            parts.append(sk)
    return " ".join(parts).lower()


# EXEMPLAR: Phase 2 reference — rule-based role_family assignment for canonical_roles.
def classify_canonical_role(
    label: str,
    *,
    representative_titles: list[str] | None = None,
    top_skills: list | None = None,
) -> str | None:
    """Assign a role_family slug from cluster metadata (rule-based; deterministic)."""
    text = _collect_classifier_text(
        label,
        representative_titles=representative_titles,
        top_skills=top_skills,
    )
    if not text.strip():
        return None

    # Score each family by alias hits in the full classifier text (longer alias = higher weight).
    best_slug: str | None = None
    best_score = 0
    for entry in load_role_family_config():
        score = 0
        for alias in entry.aliases:
            if len(alias) >= 3 and alias in text:
                score += len(alias) * max(1, len(alias.split()))
        if entry.slug == "cloud_engineering" and "cloud" in text:
            score += 15
        if score > best_score:
            best_score = score
            best_slug = entry.slug

    if best_score >= _MIN_RULE_SCORE:
        return best_slug
    return None


def classify_canonical_role_llm_batch(
    items: list[tuple[str, str]],
    *,
    agent_name: str = "analytics-role-family-backfill",
) -> dict[str, str]:
    """LLM batch classify unmapped labels. *items* = ``(role_id, label)`` pairs."""
    if not items:
        return {}

    from common.llm_adapter import complete

    slugs = [e.slug for e in load_role_family_config()]
    slug_list = ", ".join(slugs)
    lines = "\n".join(f'- role_id="{rid}" label="{lbl}"' for rid, lbl in items[:50])
    prompt = (
        "Assign each canonical role label to exactly one role_family slug from this list:\n"
        f"{slug_list}\n\n"
        'Respond with JSON only: {"assignments": [{"role_id": "...", "role_family": "..."}, ...]}\n'
        "Use null role_family only if no family fits.\n\n"
        f"Roles:\n{lines}"
    )
    try:
        raw = complete(
            prompt=prompt,
            agent_name=agent_name,
            role="classification",
        )
        text = raw if isinstance(raw, str) else str(raw)
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return {}
        parsed = json.loads(text[start:end])
        out: dict[str, str] = {}
        for row in parsed.get("assignments") or []:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("role_id") or "").strip()
            fam = row.get("role_family")
            if rid and fam and str(fam).strip() in slugs:
                out[rid] = str(fam).strip()
        return out
    except Exception as exc:
        log.warning(
            "role_family_llm_batch_failed",
            error_type=type(exc).__name__,
            error=str(exc),
            batch_size=len(items),
        )
        return {}
