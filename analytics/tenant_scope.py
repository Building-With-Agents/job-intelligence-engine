"""JIE #224 — LaborPulse ``X-Tenant-Id`` → entitled subregions + 403 for out-of-scope geography."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

# Canonical ``borderplex_subregion`` values (align with router + dbo.geo_demand_weekly)
BORDERPLEX_ENTITLED_SUBREGIONS: frozenset[str] = frozenset(
    {
        "el_paso",
        "las_cruces",
        "ciudad_juarez",
        "dona_ana",
        "regional",
    }
)

# Second tenant: disjoint from Borderplex (demo); geo_demand will typically return 0 rows.
PUGET_ENTITLED_SUBREGIONS: frozenset[str] = frozenset(
    {
        "tacoma",
        "seattle_metro",
        "bremerton",
    }
)

# Optional env: comma-separated extra tenant:region pairs (future) — not parsed in v1.

_DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    s.strip().lower() for s in (os.getenv("JIE_TENANT_ALLOWLIST", "borderplex,puget_sound") or "").split(",") if s.strip()
)

# Borderplex tenant: explicit out-of-tenant (Pacific NW); JIE #224 403 when user names that market.
_RE_BORDERPLEX_DENY: re.Pattern[str] = re.compile(
    r"\b("
    r"puget\s*sound|greater\s*seattle|seattle(\s+metro)?|"
    r"tacoma|bellingham|bremerton|spokane|olympia|everett|"
    r"king\s*county|redmond|bellevue|kirkland|renton|vancouver,\s*wa|"
    r"portland,\s*or|\bportland\s+or\b"
    r")\b",
    re.IGNORECASE,
)
# Puget demo tenant: explicit Borderplex place / names (JIE #224 403 for wrong market).
_RE_PUGET_DENY: re.Pattern[str] = re.compile(
    r"\b("
    r"el[\s-]+paso|las[\s-]+cruces|dona[\s-]+ana|dona_ana|"
    r"cd\.?\s*ju[áa]rez|ciudad[\s-]+ju[áa]rez|ju[áa]rez"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TenantAccess:
    """Entitlement for a single X-Tenant-Id."""

    tenant_id: str
    allowed_subregions: frozenset[str]
    can_query_borderplex_skill_tables: bool
    """If False, JIE has no per-tenant aggregate rows for this tenant; skill/velocity tables are empty."""


class RegionNotEntitledError(Exception):
    """User question names / implies geography outside the tenant’s entitlement."""

    def __init__(self, requested_region: str) -> None:
        super().__init__(requested_region)
        self.requested_region = requested_region


class UnknownTenantIdError(ValueError):
    """``X-Tenant-Id`` not in the configured allowlist."""


def get_tenant_access(tenant_id: str) -> TenantAccess:
    """Resolve ``X-Tenant-Id``; unknown or disallowed IDs raise :class:`UnknownTenantIdError`."""
    s = (tenant_id or "").strip().lower()
    if not s:
        raise UnknownTenantIdError("missing tenant_id")
    if s not in _DEFAULT_ALLOWLIST:
        raise UnknownTenantIdError(f"invalid tenant: {s}")
    if s == "borderplex":
        return TenantAccess(
            tenant_id="borderplex",
            allowed_subregions=frozenset(BORDERPLEX_ENTITLED_SUBREGIONS),
            can_query_borderplex_skill_tables=True,
        )
    if s == "puget_sound":
        return TenantAccess(
            tenant_id="puget_sound",
            allowed_subregions=frozenset(PUGET_ENTITLED_SUBREGIONS),
            can_query_borderplex_skill_tables=False,
        )
    raise UnknownTenantIdError(f"unconfigured tenant: {s}")


def get_tenant_access_for_pipeline(tenant_id: str | None) -> TenantAccess:
    """Local or Streamlit callers with no header default to Borderplex; never raises."""
    s = (tenant_id or "").strip().lower()
    if not s or s == "borderplex":
        return get_tenant_access("borderplex")
    try:
        return get_tenant_access(s)
    except UnknownTenantIdError:
        return get_tenant_access("borderplex")


def check_region_entitled(tenant: TenantAccess, question: str, entities: dict[str, Any] | None) -> None:
    """Raise :class:`RegionNotEntitledError` when the user clearly asks for another market (JIE #224).

    Questions with no place (e.g. "manufacturing jobs"): do not raise; SQL is scoped to the
    tenant's entitled ``borderplex_subregion`` set in :class:`QueryRouter`.
    """
    g = (entities or {}).get("geographic_terms") or []
    blob = " ".join(
        [question, *(str(x) for x in g if x)],
    ).lower()
    if tenant.tenant_id == "borderplex":
        if _RE_BORDERPLEX_DENY.search(blob):
            raise RegionNotEntitledError("pacific_northwest_or_out_of_tenant")
        return
    if tenant.tenant_id == "puget_sound" and _RE_PUGET_DENY.search(blob):
        raise RegionNotEntitledError("borderplex_region_in_puget_tenant")
    return
