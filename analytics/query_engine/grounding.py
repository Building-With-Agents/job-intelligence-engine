"""Post-generation numeric grounding against ``EvidenceBundle`` (GitHub #117).

Deterministic checks only — no LLM. See ``.cursor/rules/analytics-qna-synthesis.mdc``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final

from analytics.query_engine.schemas import EvidenceBundle

# Numbers in prose: grouped integers, plain integers, decimals; optional % suffix.
_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?%?\b|\b\d+\.\d+%?\b|\b\d+%?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GroundingResult:
    ok: bool
    unsupported_tokens: tuple[str, ...]
    reason_code: str | None = None


def _strip_number_token(raw: str) -> str:
    t = raw.strip().lower().rstrip("%")
    t = t.removesuffix("k").strip() if t.endswith("k") else t
    return t.replace(",", "")


def _numeric_value(norm: str) -> float | None:
    if not norm or not re.match(r"^-?\d", norm):
        return None
    try:
        v = float(norm)
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    return v


def _build_allowed_corpus(bundle: EvidenceBundle) -> str:
    parts: list[str] = [bundle.period_coverage or ""]
    for f in bundle.facts:
        parts.append(f.summary)
        if f.supporting_count is not None:
            parts.append(str(f.supporting_count))
        if f.time_period:
            parts.append(f.time_period)
        if f.source_table:
            parts.append(f.source_table)
    return " ".join(parts).lower()


def _collect_allowed_numeric_strings(corpus: str) -> set[str]:
    allowed: set[str] = set()
    for m in _NUMBER_PATTERN.finditer(corpus):
        raw = m.group(0)
        norm = _strip_number_token(raw)
        if norm:
            allowed.add(norm)
        # Also keep a few display variants for 72k-style if corpus used "72000"
        val = _numeric_value(norm)
        if val is not None and val == int(val) and abs(val) < 1e12:
            allowed.add(str(int(val)))
    return allowed


def _allowed_float_values(corpus: str) -> list[float]:
    vals: list[float] = []
    for m in _NUMBER_PATTERN.finditer(corpus):
        norm = _strip_number_token(m.group(0))
        v = _numeric_value(norm)
        if v is not None and math.isfinite(v):
            vals.append(v)
    return vals


def _float_matches_allowed(x: float, allowed: list[float], *, rel_tol: float = 0.005, abs_tol: float = 1.0) -> bool:
    if not math.isfinite(x):
        return False
    for y in allowed:
        if not math.isfinite(y):
            continue
        diff = abs(x - y)
        if diff <= abs_tol:
            return True
        scale = max(abs(x), abs(y), 1.0)
        if diff / scale <= rel_tol:
            return True
    return False


def verify_answer_grounding(answer_text: str, bundle: EvidenceBundle) -> GroundingResult:
    """Return whether every numeric token in ``answer_text`` is supported by the bundle.

    Matching uses a normalized digit form (commas stripped) and float proximity
    (default 0.5%% relative, $1 absolute) against numbers extracted from the corpus.
    """
    if not (answer_text or "").strip():
        return GroundingResult(ok=True, unsupported_tokens=(), reason_code=None)

    corpus = _build_allowed_corpus(bundle)
    allowed_str = _collect_allowed_numeric_strings(corpus)
    allowed_floats = _allowed_float_values(corpus)

    unsupported: list[str] = []
    for m in _NUMBER_PATTERN.finditer(answer_text):
        raw = m.group(0)
        norm = _strip_number_token(raw)
        if not norm:
            continue
        # Allow standalone year if it appears in corpus (e.g. 2025-Q1)
        if norm in allowed_str:
            continue
        val = _numeric_value(norm)
        if val is not None and _float_matches_allowed(val, allowed_floats):
            continue
        # Substring fallback: normalized digits appear inside corpus (e.g. 2025 inside 2025-q1)
        if norm and norm in corpus.replace(",", ""):
            continue
        unsupported.append(raw.strip())

    if unsupported:
        return GroundingResult(
            ok=False,
            unsupported_tokens=tuple(unsupported),
            reason_code="numeric_not_in_evidence",
        )
    return GroundingResult(ok=True, unsupported_tokens=(), reason_code=None)


def period_coverage_in_text(answer_text: str, period_coverage: str) -> bool:
    """True if ``period_coverage`` (non-empty, not unknown) appears in answer (case-insensitive)."""
    p = (period_coverage or "").strip()
    if not p or p.lower() == "period unknown":
        return True
    return p.lower() in (answer_text or "").lower()


def prefix_period_coverage(answer_text: str, period_coverage: str) -> str:
    """Prefix a data-period line when missing from ``answer_text``."""
    body = (answer_text or "").strip()
    p = (period_coverage or "").strip() or "period unknown"
    if period_coverage_in_text(body, p):
        return body
    line = f"Data period: {p}."
    return f"{line}\n\n{body}" if body else line
