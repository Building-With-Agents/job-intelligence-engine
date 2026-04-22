"""Automated scores (0.0–1.0) for golden-question Q&A eval — Week 9 harness.

Four metrics aligned with docs/Week 9/TODO.md:
``intent_accuracy``, ``evidence_citation``, ``confidence_flags``, ``latency_sla``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

# Week 8 / analytics-qna-synthesis: surface explanation when blended confidence is low.
_CONFIDENCE_TRANSPARENCY_THRESHOLD = 0.6

# Default SLA in seconds (override with QA_EVAL_LATENCY_SLA_SECONDS).
_DEFAULT_LATENCY_SLA_SECONDS = 45.0

_TOKEN_SPLIT = re.compile(r"[_\s]+")

RELATED_INTENTS: dict[str, set[str]] = {
    "geographic": {"comparison", "employer", "workflow"},
    "comparison": {"geographic", "trend"},
    "trend": {"role_evolution", "comparison", "disruption", "emergence", "curriculum"},
    "role_evolution": {"trend", "disruption", "curriculum"},
    "disruption": {"emergence", "role_evolution", "trend"},
    "emergence": {"disruption", "trend"},
    "employer": {"geographic", "workflow"},
    "curriculum": {"trend", "role_evolution"},
    "workflow": {"employer", "geographic"},
}


def _norm_intent(s: str | None) -> str:
    return (s or "").strip().lower()


@dataclass(frozen=True)
class QAItemScores:
    intent_accuracy: float
    evidence_citation: float
    confidence_flags: float
    latency_sla: float
    comments: dict[str, str]


def score_intent_accuracy(
    *,
    expected_intent: str,
    classified_intent: str,
    difficulty: str = "medium",
) -> tuple[float, str]:
    """Exact match on normalized intent; optional leniency for ``hard`` (IMP-030 hook)."""
    exp = _norm_intent(expected_intent)
    got = _norm_intent(classified_intent)
    if not exp:
        return 0.0, "missing expected_intent in metadata"
    if got == exp:
        return 1.0, "intent matches"
    if got in RELATED_INTENTS.get(exp, set()):
        return 0.5, f"related: expected {exp!r}, got {got!r}"
    return 0.0, f"mismatch: expected {exp!r}, got {got!r}"


def _keywords_from_rubric_token(token: str) -> list[str]:
    """Turn ``el_paso_subregion_filter_confirmed`` into light keywords for loose matching."""
    parts = [p for p in _TOKEN_SPLIT.split((token or "").strip().lower()) if len(p) > 1]
    # Drop very generic words
    skip = frozenset({"the", "a", "an", "or", "and", "with", "for", "from"})
    return [p for p in parts if p not in skip]


def _answer_covers_keywords(answer_lower: str, keywords: list[str], *, min_hits: int) -> bool:
    if not keywords:
        return True
    hits = sum(1 for k in keywords if k in answer_lower)
    need = min(min_hits, len(keywords))
    return hits >= need


def score_evidence_citation(
    *,
    answer: str,
    evidence: list[dict[str, Any]],
    must_include: list[str],
    must_not_include: list[str],
    refused: bool,
    sql_execution_error_detail: str | None,
    pipeline_error: str | None,
) -> tuple[float, str]:
    """Grounding + rubric heuristics (semantic tokens → keyword presence)."""
    if pipeline_error:
        return 0.0, f"pipeline_error: {pipeline_error[:200]}"
    if sql_execution_error_detail:
        return 0.0, "sql_execution_error present"
    ans = (answer or "").strip().lower()
    if not ans:
        return 0.0, "empty answer"

    if refused:
        # Refusal can be correct; reward non-empty evidence or explicit caveat in answer.
        if evidence:
            return 0.85, "refused with evidence rows"
        return 0.7 if len(ans) > 40 else 0.4, "refusal without evidence list"

    if not evidence:
        return 0.0, "no evidence items while not refused"

    # Citation coverage: overlap between answer and evidence text (multiple patterns).
    ev_blob = " ".join(f"{e.get('title', '')} {e.get('source', '')} {e.get('snippet', '')}".lower() for e in evidence)
    ans_words = {w for w in re.findall(r"[a-z0-9]{4,}", ans)}
    ev_words = {w for w in re.findall(r"[a-z0-9]{4,}", ev_blob)}
    if ans_words and ev_words:
        overlap = len(ans_words & ev_words) / max(1, min(len(ans_words), 40))
        overlap_score = min(1.0, overlap * 3.0)
    else:
        overlap_score = 0.3

    include_scores: list[float] = []
    for tok in must_include:
        kws = _keywords_from_rubric_token(tok)
        if not kws:
            continue
        need = max(1, len(kws) // 2)
        ok = _answer_covers_keywords(ans, kws, min_hits=need)
        include_scores.append(1.0 if ok else 0.35)

    must_in_avg = sum(include_scores) / len(include_scores) if include_scores else 0.75

    penalty = 0.0
    for tok in must_not_include:
        kws = _keywords_from_rubric_token(tok)
        if len(kws) >= 2 and _answer_covers_keywords(ans, kws, min_hits=len(kws)):
            penalty += 0.15
    penalty = min(0.6, penalty)

    raw = 0.45 * overlap_score + 0.45 * must_in_avg + 0.1 * (1.0 - penalty)
    return max(0.0, min(1.0, raw)), "overlap + must_include heuristics (see IMP-030 for LLM judge)"


def score_confidence_flags(
    *,
    confidence: float,
    confidence_flagged_low: bool,
    confidence_explanation: str | None,
    volume_flagged_low: bool,
    volume_warning: str | None,
) -> tuple[float, str]:
    """Reward calibration vs numeric confidence and transparency when flagged."""
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    expect_low = conf < _CONFIDENCE_TRANSPARENCY_THRESHOLD
    calibration = 1.0 if bool(confidence_flagged_low) == expect_low else 0.55

    expl = (confidence_explanation or "").strip()
    expl_ok = (1.0 if len(expl) > 12 else 0.45) if confidence_flagged_low else 1.0

    vol = (volume_warning or "").strip()
    vol_ok = (1.0 if len(vol) > 8 else 0.55) if volume_flagged_low else 1.0

    raw = 0.45 * calibration + 0.35 * expl_ok + 0.20 * vol_ok
    return max(0.0, min(1.0, raw)), "calibration vs 0.6 + explanations"


def score_latency_sla(*, latency_seconds: float, sla_seconds: float | None = None) -> tuple[float, str]:
    """Normalized score: 1.0 at or below SLA, decays above."""
    sla = float(sla_seconds or os.getenv("QA_EVAL_LATENCY_SLA_SECONDS") or _DEFAULT_LATENCY_SLA_SECONDS)
    if sla <= 0:
        sla = _DEFAULT_LATENCY_SLA_SECONDS
    lat = max(float(latency_seconds or 0.0), 1e-6)
    s = min(1.0, sla / lat)
    return max(0.0, min(1.0, s)), f"min(1, {sla:.1f}s / latency)"


def compute_item_scores(
    *,
    golden: dict[str, Any],
    response: dict[str, Any] | None,
    latency_seconds: float,
    pipeline_error: str | None,
    sla_seconds: float | None = None,
) -> QAItemScores:
    """Aggregate four scores; on failure use 0.0 with explanatory comments."""
    exp_intent = str(golden.get("intent") or golden.get("expected_intent") or "")
    difficulty = str(golden.get("difficulty") or "medium")

    if pipeline_error or response is None:
        z = QAItemScores(
            intent_accuracy=0.0,
            evidence_citation=0.0,
            confidence_flags=0.0,
            latency_sla=score_latency_sla(latency_seconds=latency_seconds, sla_seconds=sla_seconds)[0],
            comments={
                "intent_accuracy": pipeline_error or "no response",
                "evidence_citation": pipeline_error or "no response",
                "confidence_flags": pipeline_error or "no response",
                "latency_sla": "latency only (pipeline failed)",
            },
        )
        return z

    classified = str(response.get("classified_intent") or "other")
    ia, ia_c = score_intent_accuracy(
        expected_intent=exp_intent,
        classified_intent=classified,
        difficulty=difficulty,
    )

    ev_list = response.get("evidence") if isinstance(response.get("evidence"), list) else []
    ev_dicts: list[dict[str, Any]] = [e for e in ev_list if isinstance(e, dict)]
    ec, ec_c = score_evidence_citation(
        answer=str(response.get("answer") or ""),
        evidence=ev_dicts,
        must_include=list(golden.get("must_include") or []),
        must_not_include=list(golden.get("must_not_include") or []),
        refused=bool(response.get("refused")),
        sql_execution_error_detail=response.get("sql_execution_error_detail"),
        pipeline_error=pipeline_error,
    )

    cf, cf_c = score_confidence_flags(
        confidence=float(response.get("confidence") or 0.0),
        confidence_flagged_low=bool(response.get("confidence_flagged_low")),
        confidence_explanation=response.get("confidence_explanation"),
        volume_flagged_low=bool(response.get("volume_flagged_low")),
        volume_warning=response.get("volume_warning"),
    )

    ls, ls_c = score_latency_sla(latency_seconds=latency_seconds, sla_seconds=sla_seconds)

    return QAItemScores(
        intent_accuracy=ia,
        evidence_citation=ec,
        confidence_flags=cf,
        latency_sla=ls,
        comments={
            "intent_accuracy": ia_c,
            "evidence_citation": ec_c,
            "confidence_flags": cf_c,
            "latency_sla": ls_c,
        },
    )


def composite_score(scores: QAItemScores) -> float:
    """Mean of four metrics (simple baseline composite)."""
    vals = (scores.intent_accuracy, scores.evidence_citation, scores.confidence_flags, scores.latency_sla)
    return float(sum(vals) / max(1, len(vals)))


def confusion_rows(
    rows: list[tuple[str, str]],
) -> dict[tuple[str, str], int]:
    """Build counts for (expected_intent, predicted_intent) pairs."""
    out: dict[tuple[str, str], int] = {}
    for exp, pred in rows:
        key = (_norm_intent(exp) or "?"), (_norm_intent(pred) or "?")
        out[key] = out.get(key, 0) + 1
    return out
