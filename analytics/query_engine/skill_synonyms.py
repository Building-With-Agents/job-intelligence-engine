"""Query-time skill synonym groups for analytics Q&A (JIE #360).

Equivalent labels in ``config/skill_synonyms.yaml`` are collapsed when routing
skill-comparison queries against ``skill_demand_weekly``. Write-time extraction
and aggregation are unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from common.config_loader import load_yaml
from common.text_normalization import normalize_label


@dataclass(frozen=True)
class SkillSynonymGroup:
    canonical: str
    aliases: frozenset[str]


def normalize_skill_label(label: str) -> str:
    """NFKC normalize, lowercase, collapse whitespace; slash/hyphen → space."""
    text = normalize_label(label)
    return re.sub(r"[-_/]+", " ", text).strip()


@lru_cache(maxsize=1)
def _load_groups() -> tuple[SkillSynonymGroup, ...]:
    data = load_yaml("skill_synonyms")
    raw = data.get("skill_synonyms") or {}
    groups_raw = raw.get("groups") if isinstance(raw, dict) else None
    if not isinstance(groups_raw, list):
        return ()
    out: list[SkillSynonymGroup] = []
    for item in groups_raw:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases_raw = item.get("aliases") or []
        aliases = frozenset(str(a).strip() for a in aliases_raw if str(a).strip())
        out.append(SkillSynonymGroup(canonical=canonical, aliases=aliases))
    return tuple(out)


@lru_cache(maxsize=1)
def _alias_to_canonical() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for group in _load_groups():
        mapping[normalize_skill_label(group.canonical)] = group.canonical
        for alias in group.aliases:
            mapping[normalize_skill_label(alias)] = group.canonical
    return mapping


@lru_cache(maxsize=1)
def _canonical_to_group_labels() -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for group in _load_groups():
        labels = frozenset({group.canonical, *group.aliases})
        out[group.canonical] = labels
    return out


def resolve_canonical_skill(label: str) -> str:
    """Map alias → canonical; unknown labels pass through trimmed."""
    key = normalize_skill_label(label)
    if not key:
        return str(label or "").strip()
    return _alias_to_canonical().get(key, str(label).strip())


def get_synonym_group_for_term(term: str) -> frozenset[str] | None:
    """Return all labels in the synonym group for *term*, or ``None``."""
    canonical = resolve_canonical_skill(term)
    return _canonical_to_group_labels().get(canonical)


def expand_skill_query_terms(terms: list[str]) -> list[str]:
    """Expand user terms to all aliases in any matched synonym group."""
    expanded: set[str] = set()
    for term in terms:
        group = get_synonym_group_for_term(term)
        if group:
            expanded.update(group)
        elif term.strip():
            expanded.add(term.strip())
    return sorted(expanded)


def terms_share_synonym_group(terms: list[str]) -> bool:
    """True when ≥2 terms resolve to the same configured synonym canonical."""
    if len(terms) < 2:
        return False
    canonicals = {resolve_canonical_skill(t) for t in terms if str(t).strip()}
    if len(canonicals) != 1:
        return False
    canonical = next(iter(canonicals))
    return canonical in _canonical_to_group_labels()
