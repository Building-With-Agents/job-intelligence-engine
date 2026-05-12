"""Unit tests for shared LLM code extraction helpers."""

from __future__ import annotations

import re

from enrichment.resolvers.llm_code_extractor import resolve_llm_code_pick


def test_resolve_llm_code_pick_exact_match() -> None:
    picked, reason = resolve_llm_code_pick(
        "541511",
        {"541511", "541512"},
        unknown_value="unknown",
        code_pattern=re.compile(r"\b\d{2,6}\b"),
    )
    assert picked == "541511"
    assert reason == "exact_code_match"


def test_resolve_llm_code_pick_unknown_literal() -> None:
    picked, reason = resolve_llm_code_pick(
        "unknown",
        {"11"},
        unknown_value="unknown",
        code_pattern=re.compile(r"\b\d{2,6}\b"),
    )
    assert picked == "unknown"
    assert reason == "llm_said_unknown"


def test_resolve_llm_code_pick_ambiguous_substring_returns_unknown() -> None:
    picked, reason = resolve_llm_code_pick(
        "Could be 541511 or maybe 541512",
        {"541511", "541512"},
        unknown_value="unknown",
        code_pattern=re.compile(r"\b\d{2,6}\b"),
    )
    assert picked == "unknown"
    assert reason == "ambiguous_multiple_catalog_codes_in_response"


def test_resolve_llm_code_pick_regex_hit() -> None:
    picked, reason = resolve_llm_code_pick(
        "The best fit is 541511 for this posting.",
        {"541511"},
        unknown_value="unknown",
        code_pattern=re.compile(r"\b\d{2,6}\b"),
    )
    assert picked == "541511"
    assert reason in {"single_code_substring_of_response", "regex_code_in_candidate_set"}


def test_resolve_llm_code_pick_soc_digit_normalization() -> None:
    picked, reason = resolve_llm_code_pick(
        "15125200",
        {"15-1252.00"},
        unknown_value="unclassified",
        code_pattern=re.compile(r"\d{2}-\d{4}(?:\.\d{2})?"),
        enable_digit_normalization=True,
        digit_key_fn=lambda code: re.sub(r"\D", "", code),
    )
    assert picked == "15-1252.00"
    assert reason == "digit_normalized_match"
