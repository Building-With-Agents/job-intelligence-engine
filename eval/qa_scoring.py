"""Automated scores (0.0–1.0 or None) for golden-question Q&A eval — Week 9 harness.

Core four metrics (baseline composite) per docs/Week 9/TODO.md:
``intent_accuracy``, ``evidence_citation``, ``confidence_self_consistency``, ``latency_sla``.

## None semantics (JIE #263)

Infrastructure failures (pipeline crash, SQL execution error, empty answer) are
**excluded** from quality metrics rather than zeroed out.  Zeroing them would
conflate two orthogonal axes — pipeline health vs answer quality — and prevent
Week 10 pairs from distinguishing "fix the prompt" from "fix the infrastructure".

| Failure class                        | Score   | Rationale                              |
|--------------------------------------|---------|----------------------------------------|
| pipeline_error / timeout / 500       | ``None``| Infra failure; not gradable            |
| sql_execution_error_detail set       | ``None``| Infra failure on evidence_citation     |
| Empty answer (contract violation)    | ``None``| Pipeline output contract broken        |
| Committed answer with no evidence    | ``0.0`` | Real quality failure; keep as signal   |
| Correct exact intent                 | ``1.0`` | Quality signal; binary (JIE #261)     |

``latency_sla`` is the one exception: wall-clock time is computable even when the
pipeline fails, so it always returns a float.

Optional metrics ``answerability`` and ``correct_refusal`` are reported separately
from the four-metric mean. Data-backed expected intents: ``answerability`` is
``None`` (excluded) on infrastructure failure, not ``0.0`` (JIE #269). Per-item
``data_backed`` in the golden record overrides ``INTENT_TO_DATA_BACKED`` when set.
``correct_refusal`` scores intent-only (non–data-backed) items on refuse vs commit;
it is ``None`` (N/A) for data-backed expected intents.

The ``composite_score`` is the mean of the four core metrics over the **scorable
pool** — ``None`` values are excluded before averaging, so the denominator shrinks
rather than the mean being dragged down by infrastructure noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Week 8 / analytics-qna-synthesis: surface explanation when blended confidence is low.
_CONFIDENCE_TRANSPARENCY_THRESHOLD = 0.6

# Default SLA in seconds (override with QA_EVAL_LATENCY_SLA_SECONDS).
_DEFAULT_LATENCY_SLA_SECONDS = 45.0

# ``POST /analytics/query`` (LaborPulse) returns ``confidence`` as low|medium|high; eval maps to floats.
_LABORPULSE_CONFIDENCE_BUCKET: dict[str, float] = {
    "low": 0.0,
    "medium": 0.5,
    "high": 1.0,
}

_TOKEN_SPLIT = re.compile(r"[_\s]+")

# Whether the golden *expected* intent is evaluated for answerability (SQL rows).
# Only the Week 9 eval harness (Pair C) maintains this map as data/pipeline
# capabilities land; the classifier does not set this.
INTENT_TO_DATA_BACKED: dict[str, bool] = {
    "trend": False,
    "role_evolution": False,
    "emergence": False,
    "disruption": False,
    "curriculum": True,
    "employer": True,
    "workflow": True,
    "geographic": True,
    "comparison": True,
}

# Not used in ``score_intent_accuracy`` (JIE #261: binary labels only). Kept for
# offline analysis / confusion matrix interpretation; do not add to the scoring path.
RELATED_INTENTS_OFFLINE: dict[str, set[str]] = {
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


def intent_is_data_backed(golden: dict[str, Any], expected_intent: str) -> bool:
    """True if the golden item expects row-backed SQL eval; per-item ``data_backed`` overrides the map (JIE #269)."""
    v = golden.get("data_backed")
    if isinstance(v, bool):
        return v
    exp = _norm_intent(expected_intent)
    return bool(INTENT_TO_DATA_BACKED.get(exp, False))


def _expected_min_rows(golden: dict[str, Any]) -> int | None:
    v = golden.get("expected_min_rows")
    if v is None or v is False:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _refusal_appropriate(golden: dict[str, Any]) -> bool | None:
    v = golden.get("refusal_appropriate")
    return v if isinstance(v, bool) else None


@dataclass(frozen=True)
class QAItemScores:
    # Content metrics: None when excluded due to infrastructure failure (JIE #263).
    intent_accuracy: float | None
    evidence_citation: float | None
    confidence_self_consistency: float | None
    # Optional: only when ``expected_confidence_range`` is set in golden (JIE #267).
    confidence_in_expected_range: float | None
    # Latency is always computable — even on pipeline failure we have wall-clock time.
    latency_sla: float
    # Answerability: None for non-data-backed intents, or when excluded (infra) on data-backed.
    answerability: float | None
    # Intent-only: refuse vs commit; None for data-backed (N/A) or when pipeline returned nothing (JIE #269).
    correct_refusal: float | None
    comments: dict[str, str]


def score_intent_accuracy(
    *,
    expected_intent: str,
    classified_intent: str,
    difficulty: str = "medium",
) -> tuple[float, str]:
    """Binary exact match on normalized intent: 1.0 or 0.0 (JIE #261; ``difficulty`` reserved)."""
    exp = _norm_intent(expected_intent)
    got = _norm_intent(classified_intent)
    if not exp:
        return 0.0, "missing expected_intent in metadata"
    if got == exp:
        return 1.0, "intent matches"
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


def _rubric_must_and_penalty(
    ans_lower: str,
    must_include: list[str],
    must_not_include: list[str],
) -> tuple[float, float]:
    """Rubric subscores: must_include mean and accumulated must_not penalty (0..0.6)."""
    include_scores: list[float] = []
    for tok in must_include:
        kws = _keywords_from_rubric_token(tok)
        if not kws:
            continue
        need = max(1, len(kws) // 2)
        ok = _answer_covers_keywords(ans_lower, kws, min_hits=need)
        include_scores.append(1.0 if ok else 0.35)
    must_in_avg = sum(include_scores) / len(include_scores) if include_scores else 0.75
    penalty = 0.0
    for tok in must_not_include:
        kws = _keywords_from_rubric_token(tok)
        if len(kws) >= 2 and _answer_covers_keywords(ans_lower, kws, min_hits=len(kws)):
            penalty += 0.15
    penalty = min(0.6, penalty)
    return must_in_avg, penalty


def score_evidence_citation(
    *,
    answer: str,
    evidence: list[dict[str, Any]],
    must_include: list[str],
    must_not_include: list[str],
    refused: bool,
    sql_execution_error_detail: str | None,
    pipeline_error: str | None,
    intent_is_data_backed: bool = False,
    expected_intent: str = "",
) -> tuple[float | None, str]:
    """Grounding + rubric heuristics (semantic tokens → keyword presence).

    Returns ``None`` for infrastructure failures so they are excluded from run
    means rather than dragging down the quality signal (JIE #263):

    - ``pipeline_error`` set           → ``None`` (pipeline crash / timeout)
    - ``sql_execution_error_detail`` set → ``None`` (SQL infra failure)
    - empty answer                     → ``None`` (input-contract violation)
    - committed answer, no evidence    → ``0.0`` (real quality failure)
    """
    if pipeline_error:
        return None, f"excluded: pipeline_error — {pipeline_error[:200]}"
    if sql_execution_error_detail:
        return None, f"excluded: sql_execution_error — {sql_execution_error_detail[:200]}"
    ans = (answer or "").strip().lower()
    if not ans:
        return None, "excluded: empty answer (input-contract violation)"

    must_in_avg, penalty = _rubric_must_and_penalty(ans, must_include, must_not_include)

    if refused:
        # JIE #260: rubric on refusal text; strong penalty when data-backed should have committed SQL-backed answer.
        raw_r = 0.85 * must_in_avg + 0.15 * (1.0 - penalty)
        raw_r = max(0.0, min(1.0, raw_r))
        if intent_is_data_backed:
            scaled = max(0.0, min(1.0, raw_r * 0.35))
            return scaled, (
                f"data-backed refusal: rubric×0.35 (expected commit); intent={_norm_intent(expected_intent) or '?'}"
            )
        return raw_r, "intent-only refusal: rubric (must_include / must_not) without length floors"

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

    raw = 0.45 * overlap_score + 0.45 * must_in_avg + 0.1 * (1.0 - penalty)
    return max(0.0, min(1.0, raw)), "overlap + must_include heuristics (see IMP-030 for LLM judge)"


def score_confidence_self_consistency(
    *,
    confidence: float,
    confidence_flagged_low: bool,
    confidence_explanation: str | None,
    volume_flagged_low: bool,
    volume_warning: str | None,
) -> tuple[float, str]:
    """Numeric confidence vs low-confidence flag self-consistency; no length games (JIE #267)."""
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    expect_low = conf < _CONFIDENCE_TRANSPARENCY_THRESHOLD
    calibration = 1.0 if bool(confidence_flagged_low) == expect_low else 0.0

    expl = (confidence_explanation or "").strip()
    expl_ok = (1.0 if expl else 0.0) if confidence_flagged_low else 1.0

    vol = (volume_warning or "").strip()
    vol_ok = (1.0 if vol else 0.0) if volume_flagged_low else 1.0

    raw = 0.45 * calibration + 0.35 * expl_ok + 0.20 * vol_ok
    return max(0.0, min(1.0, raw)), "self-consistency vs 0.6 + non-empty explanation/volume when flagged"


# Backwards-compatible name; prefer score_confidence_self_consistency in new code.
def score_confidence_flags(
    *,
    confidence: float,
    confidence_flagged_low: bool,
    confidence_explanation: str | None,
    volume_flagged_low: bool,
    volume_warning: str | None,
) -> tuple[float, str]:
    return score_confidence_self_consistency(
        confidence=confidence,
        confidence_flagged_low=confidence_flagged_low,
        confidence_explanation=confidence_explanation,
        volume_flagged_low=volume_flagged_low,
        volume_warning=volume_warning,
    )


def expected_confidence_range_from_golden(golden: dict[str, Any]) -> tuple[float, float] | None:
    """``[lo, hi]`` on 0.0-1.0; ``None`` if not specified (JIE #267 phase B)."""
    v = golden.get("expected_confidence_range")
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    try:
        lo = float(v[0])
        hi = float(v[1])
    except (TypeError, ValueError, IndexError):
        return None
    lo = max(0.0, min(1.0, lo))
    hi = max(0.0, min(1.0, hi))
    if lo > hi:
        lo, hi = hi, lo
    return (lo, hi)


def score_confidence_in_expected_range(
    *,
    confidence: float,
    range_01: tuple[float, float] | None,
) -> tuple[float | None, str]:
    """Graduated fit to ``[lo, hi]``; ``None`` when the golden has no range."""
    if range_01 is None:
        return None, "no expected_confidence_range in golden"
    try:
        c = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        c = 0.0
    lo, hi = range_01
    if c >= lo and c <= hi:
        return 1.0, f"confidence in [{lo:.3f}, {hi:.3f}]"
    span = max(hi - lo, 0.02)
    gap = (lo - c) if c < lo else (c - hi)
    s = max(0.0, 1.0 - min(1.0, gap / span))
    return s, f"out of [{lo:.3f}, {hi:.3f}]; distance penalty"


def run_ece_from_correctness(
    confidences: list[float],
    correctness: list[bool | None],
) -> float | None:
    """ECE over items with known correctness. Returns ``None`` if labels are missing (do not fake ECE)."""
    if not confidences or not correctness or len(confidences) != len(correctness):
        return None
    if not any(c is not None for c in correctness):
        return None
    return None


def coerce_eval_response_confidence(raw: Any) -> float:
    """Turn ``response['confidence']`` into a 0.0-1.0 float.

    - In-process ``AnalyticsQueryResponse`` uses a numeric ``confidence`` (unchanged, clamped).
    - HTTP LaborPulse uses ``"low"`` / ``"medium"`` / ``"high"``; mapped to 0.0 / 0.5 / 1.0 for scoring.
    """
    if raw is None or raw == "":
        return 0.0
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in _LABORPULSE_CONFIDENCE_BUCKET:
            return _LABORPULSE_CONFIDENCE_BUCKET[key]
        try:
            return max(0.0, min(1.0, float(key)))
        except ValueError:
            return 0.0
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def score_latency_sla(*, latency_seconds: float, sla_seconds: float | None = None) -> tuple[float, str]:
    """Normalized score: 1.0 at or below SLA, decays above."""
    from eval._config import qa_latency_sla_seconds

    sla = float(sla_seconds or qa_latency_sla_seconds())
    if sla <= 0:
        sla = _DEFAULT_LATENCY_SLA_SECONDS
    lat = max(float(latency_seconds or 0.0), 1e-6)
    s = min(1.0, sla / lat)
    return max(0.0, min(1.0, s)), f"min(1, {sla:.1f}s / latency)"


def score_answerability(
    *,
    expected_intent: str,
    response: dict[str, Any] | None,
    pipeline_error: str | None,
    data_backed: bool,
    expected_min_rows: int | None = None,
    zero_rows_is_correct: bool = False,
) -> tuple[float | None, str]:
    """1.0 / 0.0 for data-backed items with a gradable response; ``None`` if skipped or not gradable (JIE #269).

    On ``pipeline_error`` or missing ``response`` for a data-backed item, returns
    ``None`` (same #263-style exclusion as other content metrics) — not ``0.0``.

    When ``expected_min_rows`` is set, require ``row_count_returned >= expected_min_rows``,
    unless ``zero_rows_is_correct`` and ``row_count_returned == 0`` (e.g. no matches in region).
    """
    exp = _norm_intent(expected_intent)
    if not exp:
        return None, "skipped: missing expected_intent in metadata"
    if not data_backed:
        return None, f"skipped: intent {exp!r} not data-backed in harness (use data_backed in golden to override)"

    if pipeline_error or response is None:
        return None, f"excluded: not gradable — {pipeline_error or 'no response'}"

    try:
        rc = int(response.get("row_count_returned") or 0)
    except (TypeError, ValueError):
        rc = 0

    if expected_min_rows is not None and expected_min_rows > 0:
        if zero_rows_is_correct and rc == 0:
            return 1.0, "row_count=0; zero_rows_is_correct (expected empty cohort)"
        if rc >= expected_min_rows:
            return 1.0, f"row_count_returned={rc} (>= {expected_min_rows})"
        return 0.0, f"row_count_returned={rc} (below {expected_min_rows})"

    if zero_rows_is_correct and rc == 0:
        return 1.0, "row_count=0; zero_rows_is_correct"
    if rc > 0:
        return 1.0, f"row_count_returned={rc} (data-backed intent)"
    return 0.0, "row_count_returned=0 (data-backed intent, no SQL rows)"


def score_correct_refusal(
    *,
    expected_intent: str,
    intent_data_backed: bool,
    refused: bool,
    question: str,
    refusal_appropriate: bool | None = None,
    no_response: bool = False,
) -> tuple[float | None, str]:
    """1.0 / 0.0 for intent-only (non–data-backed) items; ``None`` if N/A (JIE #269).

    Data-backed expected intents: not applicable (``None``) — use answerability and evidence.
    When the pipeline did not return a body to evaluate, returns ``None``.

    If ``refusal_appropriate`` is set on the golden row, ``True`` means the item expects a
    refusal; ``False`` means it expects a committed answer. If omitted, the default is to
    prefer a committed answer for intent-only items (``refused`` → 0.0). Empty question:
    returns ``None``.
    """
    if no_response:
        return None, "excluded: no response (cannot evaluate)"
    if intent_data_backed:
        return None, "N/A: data-backed expected intent (use answerability + evidence path)"
    if not (question or "").strip():
        return None, "skipped: empty question"
    exp = _norm_intent(expected_intent)
    if not exp:
        return None, "skipped: missing expected_intent in metadata"
    if refusal_appropriate is not None:
        if refusal_appropriate:
            return (
                (1.0, "expected refusal, got refusal") if refused else (0.0, "expected refusal, got committed answer")
            )
        return (
            (0.0, "expected committed answer, got refusal")
            if refused
            else (1.0, "expected committed answer, got answer")
        )
    if refused:
        return 0.0, "intent-only: default assumes committed answer (set refusal_appropriate in golden to override)"
    return 1.0, "intent-only: committed answer (default)"


def compute_item_scores(
    *,
    golden: dict[str, Any],
    response: dict[str, Any] | None,
    latency_seconds: float,
    pipeline_error: str | None,
    sla_seconds: float | None = None,
) -> QAItemScores:
    """Aggregate four core scores and optional answerability.

    On infrastructure failure (``pipeline_error`` or no ``response``), content
    metrics are set to ``None`` so they are excluded from run means.  Only
    ``latency_sla`` is always computed (JIE #263).
    """
    exp_intent = str(golden.get("intent") or golden.get("expected_intent") or "")
    difficulty = str(golden.get("difficulty") or "medium")
    q_text = str(golden.get("question") or "")
    dback = intent_is_data_backed(golden, exp_intent)
    emn = _expected_min_rows(golden)
    zrc = bool(golden.get("zero_rows_is_correct", False))
    rapt = _refusal_appropriate(golden)
    ecr = expected_confidence_range_from_golden(golden)

    ls, ls_c = score_latency_sla(latency_seconds=latency_seconds, sla_seconds=sla_seconds)

    if pipeline_error or response is None:
        an, an_c = score_answerability(
            expected_intent=exp_intent,
            response=None,
            pipeline_error=pipeline_error,
            data_backed=dback,
            expected_min_rows=emn,
            zero_rows_is_correct=zrc,
        )
        cr, cr_c = score_correct_refusal(
            expected_intent=exp_intent,
            intent_data_backed=dback,
            refused=False,
            question=q_text,
            refusal_appropriate=rapt,
            no_response=True,
        )
        reason = pipeline_error or "no response"
        cie, cie_c = None, f"excluded: {reason}"
        return QAItemScores(
            intent_accuracy=None,
            evidence_citation=None,
            confidence_self_consistency=None,
            confidence_in_expected_range=cie,
            latency_sla=ls,
            answerability=an,
            correct_refusal=cr,
            comments={
                "intent_accuracy": f"excluded: {reason}",
                "evidence_citation": f"excluded: {reason}",
                "confidence_self_consistency": f"excluded: {reason}",
                "confidence_in_expected_range": cie_c,
                "latency_sla": f"{ls_c} (content metrics excluded — pipeline failure)",
                "answerability": an_c,
                "correct_refusal": cr_c,
            },
        )

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
        intent_is_data_backed=dback,
        expected_intent=exp_intent,
    )

    cnum = coerce_eval_response_confidence(response.get("confidence"))
    cf, cf_c = score_confidence_self_consistency(
        confidence=cnum,
        confidence_flagged_low=bool(response.get("confidence_flagged_low")),
        confidence_explanation=response.get("confidence_explanation"),
        volume_flagged_low=bool(response.get("volume_flagged_low")),
        volume_warning=response.get("volume_warning"),
    )
    cie, cie_c = score_confidence_in_expected_range(
        confidence=cnum,
        range_01=ecr,
    )

    an, an_c = score_answerability(
        expected_intent=exp_intent,
        response=response,
        pipeline_error=pipeline_error,
        data_backed=dback,
        expected_min_rows=emn,
        zero_rows_is_correct=zrc,
    )
    cr, cr_c = score_correct_refusal(
        expected_intent=exp_intent,
        intent_data_backed=dback,
        refused=bool(response.get("refused")),
        question=q_text,
        refusal_appropriate=rapt,
        no_response=False,
    )

    return QAItemScores(
        intent_accuracy=ia,
        evidence_citation=ec,
        confidence_self_consistency=cf,
        confidence_in_expected_range=cie,
        latency_sla=ls,
        answerability=an,
        correct_refusal=cr,
        comments={
            "intent_accuracy": ia_c,
            "evidence_citation": ec_c,
            "confidence_self_consistency": cf_c,
            "confidence_in_expected_range": cie_c,
            "latency_sla": ls_c,
            "answerability": an_c,
            "correct_refusal": cr_c,
        },
    )


def composite_score(scores: QAItemScores) -> float:
    """Mean of scorable core metrics only (excludes ``answerability`` and ``None`` values).

    The four are intent, evidence, self-consistency, latency — not ``confidence_in_expected_range`` (JIE #267).
    ``latency_sla`` is always a float so the denominator is always ≥ 1.
    Content metrics excluded due to infrastructure failures (JIE #263) are
    dropped from the average — the denominator shrinks rather than the mean
    being dragged down by pipeline noise.
    """
    candidates = (
        scores.intent_accuracy,
        scores.evidence_citation,
        scores.confidence_self_consistency,
        scores.latency_sla,
    )
    vals = [v for v in candidates if v is not None]
    return float(sum(vals) / len(vals))


def confusion_rows(
    rows: list[tuple[str, str]],
) -> dict[tuple[str, str], int]:
    """Build counts for (expected_intent, predicted_intent) pairs."""
    out: dict[tuple[str, str], int] = {}
    for exp, pred in rows:
        key = (_norm_intent(exp) or "?"), (_norm_intent(pred) or "?")
        out[key] = out.get(key, 0) + 1
    return out


def _mean_metric_on_rows(
    rows: list[tuple[Any, Any, Any]],
    key: str,
) -> float | None:
    from statistics import fmean

    xs: list[float] = []
    for _id, sc, _err in rows:
        v = getattr(sc, key, None)
        if isinstance(v, (int, float)):
            xs.append(float(v))
    if not xs:
        return None
    return float(fmean(xs))


def _geometric_mean_four(a: float | None, b: float | None, c: float | None, d: float | None) -> float | None:
    if a is None or b is None or c is None or d is None:
        return None
    p = max(1e-9, a) * max(1e-9, b) * max(1e-9, c) * max(1e-9, d)
    return float(p**0.25)


def subcomposites_from_means(
    *,
    evidence_citation: float | None,
    intent_accuracy: float | None,
    latency_sla: float | None,
    answerability: float | None,
    correct_refusal: float | None,
    confidence_self_consistency: float | None,
    confidence_in_expected_range: float | None,
    n_data_backed_answerability: int = 0,
) -> dict[str, float | bool | str | None]:
    """Shared JIE #268 engine from per-metric means (local rows or Langfuse run aggregates)."""
    e_mean = evidence_citation
    i_mean = intent_accuracy
    l_mean = latency_sla
    a_mean = answerability
    r_mean = correct_refusal
    csc = confidence_self_consistency
    cie = confidence_in_expected_range
    if l_mean is None:
        l_mean = 0.0
    if a_mean is not None and r_mean is not None:
        ph = 0.40 * a_mean + 0.35 * l_mean + 0.25 * r_mean
    elif a_mean is not None:
        ph = 0.60 * a_mean + 0.40 * l_mean
    elif r_mean is not None:
        ph = 0.45 * r_mean + 0.55 * l_mean
    else:
        ph = l_mean

    if cie is not None and csc is not None:
        sfty = 0.65 * csc + 0.35 * cie
    elif csc is not None:
        sfty = csc
    else:
        sfty = None

    overall = _geometric_mean_four(e_mean, i_mean, ph, sfty)

    th = float(os.getenv("QA_EVAL_ANSWERABILITY_GATE_THRESHOLD", "0.2"))
    n_ab = max(0, n_data_backed_answerability)
    gated = bool(a_mean is not None and n_ab > 0 and float(a_mean) < th)
    gmsg = f"answerability {a_mean} < {th} (n_data_backed={n_ab})" if gated else "ok"

    return {
        "prompt_quality_composite": e_mean,
        "classification_composite": i_mean,
        "pipeline_health_composite": ph,
        "safety_composite": sfty,
        "overall_geometric_composite": overall,
        "gated": gated,
        "gate_message": gmsg,
    }


def run_subcomposites_and_gates(
    rows: list[tuple[Any, Any, Any]],
) -> dict[str, float | bool | str | None]:
    """JIE #268: four sub-composites, overall geometric mean, and answerability gate from eval rows.

    ``prompt_quality`` proxies the full ``evidence_citation`` mean until #265 decomposes it.
    """
    if not rows:
        return {
            "prompt_quality_composite": None,
            "classification_composite": None,
            "pipeline_health_composite": None,
            "safety_composite": None,
            "overall_geometric_composite": None,
            "gated": False,
            "gate_message": "no items",
        }
    n_ab = sum(1 for r in rows if r[1].answerability is not None)
    return subcomposites_from_means(
        evidence_citation=_mean_metric_on_rows(rows, "evidence_citation"),
        intent_accuracy=_mean_metric_on_rows(rows, "intent_accuracy"),
        latency_sla=_mean_metric_on_rows(rows, "latency_sla"),
        answerability=_mean_metric_on_rows(rows, "answerability"),
        correct_refusal=_mean_metric_on_rows(rows, "correct_refusal"),
        confidence_self_consistency=_mean_metric_on_rows(rows, "confidence_self_consistency"),
        confidence_in_expected_range=_mean_metric_on_rows(rows, "confidence_in_expected_range"),
        n_data_backed_answerability=n_ab,
    )
