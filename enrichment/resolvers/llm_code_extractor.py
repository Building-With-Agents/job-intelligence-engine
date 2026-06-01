"""Shared helpers for mapping noisy LLM text to classifier candidate codes."""

from __future__ import annotations

import re
from collections.abc import Callable


def _digits_to_canonical_codes(
    candidate_codes: set[str],
    digit_key_fn: Callable[[str], str],
) -> dict[str, str]:
    """Map a digit-normalized key to one canonical candidate code when unambiguous."""
    buckets: dict[str, list[str]] = {}
    for code in candidate_codes:
        key = digit_key_fn(code)
        if not key:
            continue
        buckets.setdefault(key, []).append(code)
    return {key: values[0] for key, values in buckets.items() if len(values) == 1}


def resolve_llm_code_pick(
    raw: str,
    candidate_codes: set[str],
    *,
    unknown_value: str,
    code_pattern: re.Pattern[str],
    enable_digit_normalization: bool = False,
    digit_key_fn: Callable[[str], str] | None = None,
) -> tuple[str, str]:
    """Return ``(picked_code_or_unknown, reason_tag)`` after candidate-set validation."""
    text = (raw or "").strip()
    if not text:
        return unknown_value, "empty_llm_response"
    if text.lower() == unknown_value.lower():
        return unknown_value, f"llm_said_{unknown_value}"
    if text in candidate_codes:
        return text, "exact_code_match"

    unquoted = text.strip("`\"'")
    if unquoted in candidate_codes:
        return unquoted, "exact_after_strip_quotes"

    digit_map: dict[str, str] = {}
    if enable_digit_normalization and digit_key_fn is not None:
        digit_map = _digits_to_canonical_codes(candidate_codes, digit_key_fn)
        raw_key = digit_key_fn(text)
        if raw_key and raw_key in digit_map:
            return digit_map[raw_key], "digit_normalized_match"

    contained = [code for code in candidate_codes if code and code in text]
    if len(contained) == 1:
        return contained[0], "single_code_substring_of_response"
    if len(contained) > 1:
        return unknown_value, "ambiguous_multiple_catalog_codes_in_response"

    regex_hits = [m.group(0) for m in code_pattern.finditer(text) if m.group(0) in candidate_codes]
    unique_hits = list(dict.fromkeys(regex_hits))
    if len(unique_hits) == 1:
        return unique_hits[0], "regex_code_in_candidate_set"

    if digit_map and digit_key_fn is not None:
        for match in code_pattern.finditer(text):
            key = digit_key_fn(match.group(0))
            if key and key in digit_map:
                return digit_map[key], "regex_then_digit_normalized"

    return unknown_value, "no_candidate_matched_llm_output"
