"""Golden-question cohort allowlists for Q&A eval (Pair C Week 10).

``--golden-ids`` overrides ``--cohort`` when the parsed ID list is non-empty.
See ``eval/qa_eval.py`` argparse help for precedence.
"""

from __future__ import annotations

from typing import Any

PAIR_C_GEO_COMP_COHORT = "pair-c-geo-comp"
PAIR_C_GEO_COMP_IDS: tuple[str, ...] = tuple(f"gq-{i:03d}" for i in range(41, 61))


def parse_golden_ids_csv(raw: str | None) -> list[str]:
    """Split comma-separated golden ids; trim whitespace; drop empties."""
    if not raw or not str(raw).strip():
        return []
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def resolve_cohort_allowed_ids(
    *,
    cohort: str | None,
    golden_ids_csv: str | None,
) -> list[str] | None:
    """Return allowlist of golden ``id`` strings, or ``None`` for full corpus.

    Precedence:
    1. If ``golden_ids_csv`` parses to a non-empty list → use those ids (caller sorts).
    2. Else if ``cohort`` is ``pair-c-geo-comp`` → fixed Pair C geographic + comparison ids.
    3. Else if ``cohort`` is set → unknown cohort, raise.
    4. Else → ``None`` (full corpus, subject to ``--limit`` in the caller).
    """
    explicit = parse_golden_ids_csv(golden_ids_csv)
    if explicit:
        return explicit
    if cohort is None or not str(cohort).strip():
        return None
    c = str(cohort).strip()
    if c == PAIR_C_GEO_COMP_COHORT:
        return list(PAIR_C_GEO_COMP_IDS)
    raise ValueError(f"Unknown --cohort {c!r}; supported: {PAIR_C_GEO_COMP_COHORT!r}")


def filter_and_sort_golden_questions(
    all_questions: list[dict[str, Any]],
    allowed_ids: list[str],
) -> list[dict[str, Any]]:
    """Return golden rows for ``allowed_ids``, sorted lexicographically by ``id``.

    Raises ``ValueError`` if any id is missing from the corpus.
    """
    golden_by_id = {str(r["id"]): r for r in all_questions}
    missing = [i for i in allowed_ids if i not in golden_by_id]
    if missing:
        raise ValueError(f"Golden file missing ids (check --golden-ids / --cohort): {missing}")
    rows = [golden_by_id[i] for i in allowed_ids]
    return sorted(rows, key=lambda r: str(r["id"]))
