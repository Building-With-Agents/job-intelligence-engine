"""Unit tests for analytics.canonical_roles.role_family (JIE #362)."""

from __future__ import annotations

from unittest.mock import patch

from analytics.canonical_roles.role_family import (
    classify_canonical_role,
    comparison_should_use_role_family_count,
    load_role_family_config,
    question_signals_job_posting_count,
    resolve_role_families_from_text,
)


def test_load_role_family_config_has_expected_slugs() -> None:
    families = load_role_family_config()
    slugs = {f.slug for f in families}
    assert "cybersecurity" in slugs
    assert "cloud_engineering" in slugs
    assert len(slugs) >= 10


def test_resolve_cloud_and_cyber_from_text() -> None:
    got = resolve_role_families_from_text(
        "cloud-engineering roles compared to cybersecurity roles",
    )
    assert "cloud_engineering" in got
    assert "cybersecurity" in got


def test_resolve_cloud_computing_alias() -> None:
    got = resolve_role_families_from_text("Cloud Computing", "Cybersecurity")
    assert "cloud_engineering" in got
    assert "cybersecurity" in got


def test_classify_cloud_infrastructure_label() -> None:
    fam = classify_canonical_role("Senior Cloud Infrastructure Engineer")
    assert fam == "cloud_engineering"


def test_comparison_requires_two_families() -> None:
    q = "How many Borderplex job postings are for cybersecurity roles?"
    assert question_signals_job_posting_count(q)
    assert not comparison_should_use_role_family_count(q, [], ["Cybersecurity"])


def test_comparison_two_families_and_job_count() -> None:
    q = (
        "How many Borderplex job postings are for cloud-engineering roles "
        "compared to cybersecurity roles in recent weeks?"
    )
    assert comparison_should_use_role_family_count(
        q,
        ["cloud engineering", "cybersecurity"],
        [],
    )


def test_classify_llm_batch_mocked() -> None:
    from analytics.canonical_roles.role_family import classify_canonical_role_llm_batch

    payload = '{"assignments": [{"role_id": "r1", "role_family": "cybersecurity"}]}'
    with patch("common.llm_adapter.complete", return_value=payload):
        out = classify_canonical_role_llm_batch([("r1", "Security Analyst")])
    assert out.get("r1") == "cybersecurity"
