"""JIE #224 — ``check_region_entitled`` for Borderplex / Puget tenants (Week 10 red-team)."""

from __future__ import annotations

import pytest

from analytics.tenant_scope import (
    RegionNotEntitledError,
    TenantAccess,
    check_region_entitled,
    get_tenant_access,
)


@pytest.fixture
def borderplex() -> TenantAccess:
    return get_tenant_access("borderplex")


def test_borderplex_seattle_in_question_raises(borderplex: TenantAccess) -> None:
    with pytest.raises(RegionNotEntitledError, match="pacific_northwest_or_out_of_tenant"):
        check_region_entitled(
            borderplex,
            "What is the median salary for software engineers in Seattle?",
            None,
        )


def test_borderplex_seattle_in_geo_terms_raises(borderplex: TenantAccess) -> None:
    with pytest.raises(RegionNotEntitledError, match="pacific_northwest_or_out_of_tenant"):
        check_region_entitled(
            borderplex,
            "Show trends for the metro area.",
            {"geographic_terms": ["Seattle"]},
        )


def test_borderplex_el_paso_ok(borderplex: TenantAccess) -> None:
    check_region_entitled(
        borderplex,
        "Registered nurse demand in El Paso last quarter",
        {"geographic_terms": ["El Paso"]},
    )


def test_borderplex_no_place_ok(borderplex: TenantAccess) -> None:
    check_region_entitled(borderplex, "Manufacturing job postings trend", None)


@pytest.mark.parametrize(
    "question",
    [
        "For the San Francisco Bay Area, show me week-over-week growth in AI/ML job postings.",
        "Compare registered nurse demand between El Paso and Houston.",
        "What is the median salary for software engineers in Dallas?",
        "Tech hiring trends in New York City for Q1.",
    ],
)
def test_check_region_entitled_raises_for_major_out_of_scope_metro(borderplex: TenantAccess, question: str) -> None:
    with pytest.raises(RegionNotEntitledError):
        check_region_entitled(borderplex, question, {"geographic_terms": []})


def test_el_paso_comparison_allowed(borderplex: TenantAccess) -> None:
    """El Paso + Las Cruces (in-tenant) must not trip the out-of-market deny list."""
    check_region_entitled(
        borderplex,
        "Compare nurse hiring in El Paso vs Las Cruces last quarter.",
        {"geographic_terms": ["El Paso", "Las Cruces"]},
    )


def test_puget_sound_el_paso_raises() -> None:
    tenant = get_tenant_access("puget_sound")
    with pytest.raises(RegionNotEntitledError, match="borderplex_region_in_puget_tenant"):
        check_region_entitled(
            tenant,
            "Compare welder pay in El Paso vs Tacoma",
            None,
        )


def test_puget_sound_seattle_ok() -> None:
    tenant = get_tenant_access("puget_sound")
    check_region_entitled(
        tenant,
        "Software engineer openings in Seattle this month",
        None,
    )
