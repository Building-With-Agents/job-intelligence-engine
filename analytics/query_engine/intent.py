"""Intent classification for natural-language analytics queries (Pair A–C wiring)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class QueryIntentKind(str, Enum):
    """High-level routing bucket for SQL template / synthesis."""

    SKILL_DEMAND = "skill_demand"
    GEO_DEMAND = "geo_demand"
    ROLE_SNAPSHOT = "role_snapshot"
    SECTOR = "sector"
    VELOCITY = "velocity"
    CO_OCCURRENCE = "co_occurrence"
    GENERIC_AGGREGATE = "generic_aggregate"


@dataclass(frozen=True)
class QueryIntent:
    kind: QueryIntentKind
    raw_query: str


_SKILL_PAT = re.compile(
    r"\b(skill|skills|esco|demand\s+for)\b",
    re.IGNORECASE,
)
_GEO_PAT = re.compile(
    r"\b(borderplex|el\s*paso|las\s*cruces|juarez|juárez|regional|geo|location)\b",
    re.IGNORECASE,
)
_ROLE_PAT = re.compile(r"\b(role|canonical|cluster|title)\b", re.IGNORECASE)
_SECTOR_PAT = re.compile(r"\b(sector|industry|naics)\b", re.IGNORECASE)
_VEL_PAT = re.compile(r"\b(velocity|trend|week\s*over\s*week|wow)\b", re.IGNORECASE)
_CO_PAT = re.compile(r"\b(co[- ]?occurrence|pair|together)\b", re.IGNORECASE)


def classify_intent(user_query: str) -> QueryIntent:
    q = (user_query or "").strip()
    if not q:
        return QueryIntent(kind=QueryIntentKind.GENERIC_AGGREGATE, raw_query=q)

    if _GEO_PAT.search(q):
        return QueryIntent(kind=QueryIntentKind.GEO_DEMAND, raw_query=q)
    if _VEL_PAT.search(q):
        return QueryIntent(kind=QueryIntentKind.VELOCITY, raw_query=q)
    if _CO_PAT.search(q):
        return QueryIntent(kind=QueryIntentKind.CO_OCCURRENCE, raw_query=q)
    if _SECTOR_PAT.search(q):
        return QueryIntent(kind=QueryIntentKind.SECTOR, raw_query=q)
    if _ROLE_PAT.search(q):
        return QueryIntent(kind=QueryIntentKind.ROLE_SNAPSHOT, raw_query=q)
    if _SKILL_PAT.search(q):
        return QueryIntent(kind=QueryIntentKind.SKILL_DEMAND, raw_query=q)

    return QueryIntent(kind=QueryIntentKind.GENERIC_AGGREGATE, raw_query=q)
