"""Shared text normalization helpers."""

from __future__ import annotations

import re
import unicodedata


def normalize_label(value: str | None) -> str:
    """NFKC normalize, lowercase, strip outer whitespace, collapse internal whitespace."""
    text = unicodedata.normalize("NFKC", value or "")
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text
