"""Tests for JIE #224 ``check_region_entitled`` (Borderplex vs out-of-tenant geography)."""

from __future__ import annotations

import pytest

from analytics.tenant_scope import (
    RegionNotEntitledError,
    check_region_entitled,
    get_tenant_access,
)


def test_borderplex_seattle_in_question_raises() -> None:
    tenant = get_tenant_access("borderplex")
    with pytest.raises(RegionNotEntitledError, match="pacific_northwest_or_out_of_tenant"):
        check_region_entitled(
            tenant,
            "What is the median salary for software engineers in Seattle?",
            None,
        )


def test_borderplex_seattle_in_geo_terms_raises() -> None:
    tenant = get_tenant_access("borderplex")
    with pytest.raises(RegionNotEntitledError, match="pacific_northwest_or_out_of_tenant"):
        check_region_entitled(
            tenant,
            "Show trends for the metro area.",
            {"geographic_terms": ["Seattle"]},
        )


def test_borderplex_el_paso_ok() -> None:
    tenant = get_tenant_access("borderplex")
    check_region_entitled(
        tenant,
        "Registered nurse demand in El Paso last quarter",
        {"geographic_terms": ["El Paso"]},
    )


def test_borderplex_no_place_ok() -> None:
    tenant = get_tenant_access("borderplex")
    check_region_entitled(tenant, "Manufacturing job postings trend", None)


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
