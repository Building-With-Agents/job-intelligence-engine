"""Unit tests for analytics.query_engine.skill_synonyms (JIE #360)."""

from __future__ import annotations

from analytics.query_engine.skill_synonyms import (
    expand_skill_query_terms,
    resolve_canonical_skill,
    terms_share_synonym_group,
)


def test_resolve_canonical_skill_ci_cd_and_continuous_integration() -> None:
    assert resolve_canonical_skill("CI/CD") == resolve_canonical_skill("Continuous Integration")
    assert resolve_canonical_skill("Continuous Integration") == "Continuous Integration"


def test_terms_share_synonym_group_ci_variants() -> None:
    assert terms_share_synonym_group(["CI/CD", "Continuous Integration"]) is True


def test_terms_share_synonym_group_different_skills() -> None:
    assert terms_share_synonym_group(["Cloud Computing", "Cybersecurity"]) is False


def test_expand_skill_query_terms_includes_aliases() -> None:
    expanded = expand_skill_query_terms(["CI/CD"])
    assert "Continuous Integration" in expanded
    assert "CI/CD" in expanded
