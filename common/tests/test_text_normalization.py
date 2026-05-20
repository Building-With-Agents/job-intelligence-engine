"""Unit tests for shared text normalization helpers."""

from __future__ import annotations

from common.text_normalization import normalize_label


def test_normalize_label_idempotent() -> None:
    raw = "  Senior\tDATA   Engineer \n"
    once = normalize_label(raw)
    twice = normalize_label(once)
    assert twice == once


def test_normalize_label_handles_none_and_empty() -> None:
    assert normalize_label(None) == ""
    assert normalize_label("") == ""


def test_normalize_label_applies_nfkc_and_lowercase() -> None:
    # Fullwidth characters should normalize to ASCII-equivalent code points.
    assert normalize_label(" ＡＢＡＰ ") == "abap"


def test_normalize_label_collapses_internal_whitespace() -> None:
    assert normalize_label("Machine   \n Learning\tEngineer") == "machine learning engineer"
