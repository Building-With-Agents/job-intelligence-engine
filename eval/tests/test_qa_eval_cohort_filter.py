"""Offline tests for Pair C golden cohort resolution (JIE #340)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.qa_eval_cohorts import (
    PAIR_C_GEO_COMP_COHORT,
    filter_and_sort_golden_questions,
    resolve_cohort_allowed_ids,
)


@pytest.fixture
def golden_path() -> Path:
    return Path(__file__).resolve().parent.parent / "qa_golden_questions.json"


@pytest.fixture
def all_questions(golden_path: Path) -> list:
    with open(golden_path, encoding="utf-8") as fh:
        return json.load(fh)


def test_pair_c_geo_comp_expands_twenty_lex_order(all_questions: list) -> None:
    ids = resolve_cohort_allowed_ids(cohort=PAIR_C_GEO_COMP_COHORT, golden_ids_csv=None)
    assert ids is not None
    assert len(ids) == 20
    assert ids == [f"gq-{i:03d}" for i in range(41, 61)]
    rows = filter_and_sort_golden_questions(all_questions, ids)
    assert len(rows) == 20
    got = [str(r["id"]) for r in rows]
    assert got == sorted(got)
    assert got[0] == "gq-041" and got[-1] == "gq-060"


def test_golden_ids_override_cohort(all_questions: list) -> None:
    ids = resolve_cohort_allowed_ids(
        cohort=PAIR_C_GEO_COMP_COHORT,
        golden_ids_csv="gq-060,gq-041",
    )
    assert ids == ["gq-060", "gq-041"]
    rows = filter_and_sort_golden_questions(all_questions, ids)
    assert [r["id"] for r in rows] == ["gq-041", "gq-060"]


def test_golden_ids_missing_raises(all_questions: list) -> None:
    with pytest.raises(ValueError, match="Golden file missing ids"):
        filter_and_sort_golden_questions(all_questions, ["gq-041", "gq-999-missing"])


def test_unknown_cohort_raises() -> None:
    with pytest.raises(ValueError, match="Unknown --cohort"):
        resolve_cohort_allowed_ids(cohort="not-a-real-cohort", golden_ids_csv=None)
