"""Unit tests for batch_ingest.py helpers (issues #157, #165).

Covers:
- `_expand_queries` 3D expansion: query × keyword × location (issue #165)
- Unknown / null tier fallback — still iterates keywords
- Tier override and no-locations flags
- Env propagation from YAML throttle block

These tests do not hit the DB or the JSearch API — pure helper behavior.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

batch_ingest = importlib.import_module("scripts.batch_ingest")


def _queries_fixture() -> list[dict]:
    return [
        {"name": "q-a", "keywords": ["a1", "a2"], "location_tier": "tier_1"},
        {"name": "q-b", "keywords": ["b1"], "location_tier": "tier_2"},
        {"name": "q-c", "keywords": ["c1", "c2", "c3"], "location_tier": None},
        {"name": "q-d", "keywords": ["d1"], "location_tier": "does_not_exist"},
        {"name": "q-e", "keywords": ["e1"]},  # no location_tier key
    ]


def _tiers_fixture() -> dict[str, list[str]]:
    return {
        "tier_1": ["El Paso, TX", "Las Cruces, NM"],
        "tier_2": ["SF, CA", "Seattle, WA"],
    }


def test_expand_queries_3d_cartesian_keyword_x_location() -> None:
    """Each (keyword, location) pair becomes one entry with a single-element keywords list."""
    queries = _queries_fixture()[:2]  # q-a (2 kw × 2 loc) + q-b (1 kw × 2 loc)
    out = batch_ingest._expand_queries(queries, _tiers_fixture())
    assert len(out) == (2 * 2) + (1 * 2)  # 6

    # Every emitted query dict holds exactly one keyword.
    for q, _loc in out:
        assert len(q["keywords"]) == 1

    # q-a fans to a1×ElPaso, a1×LasCruces, a2×ElPaso, a2×LasCruces
    a_pairs = [(q["keywords"][0], loc) for q, loc in out if q["name"] == "q-a"]
    assert a_pairs == [
        ("a1", "El Paso, TX"),
        ("a1", "Las Cruces, NM"),
        ("a2", "El Paso, TX"),
        ("a2", "Las Cruces, NM"),
    ]


def test_expand_queries_null_tier_still_iterates_keywords() -> None:
    """location_tier: null means 1 entry per keyword with empty location (no geo)."""
    queries = [_queries_fixture()[2]]  # q-c with 3 keywords, null tier
    out = batch_ingest._expand_queries(queries, _tiers_fixture())
    assert len(out) == 3
    keys = [(q["keywords"][0], loc) for q, loc in out]
    assert keys == [("c1", ""), ("c2", ""), ("c3", "")]


def test_expand_queries_unknown_tier_falls_back_to_single_location() -> None:
    """Unknown tier name → one call per keyword with empty location + warning logged."""
    queries = [_queries_fixture()[3]]  # q-d: 1 keyword, tier "does_not_exist"
    out = batch_ingest._expand_queries(queries, _tiers_fixture())
    assert len(out) == 1
    assert out[0][0]["keywords"] == ["d1"]
    assert out[0][1] == ""


def test_expand_queries_no_tier_key_treated_as_null() -> None:
    queries = [_queries_fixture()[4]]  # q-e: no location_tier key
    out = batch_ingest._expand_queries(queries, _tiers_fixture())
    assert len(out) == 1
    assert out[0][0]["keywords"] == ["e1"]
    assert out[0][1] == ""


def test_expand_queries_tier_override_applies_to_all() -> None:
    """--location-tier tier_2 forces every query onto tier_2 regardless of YAML tag."""
    queries = _queries_fixture()[:3]  # q-a(2), q-b(1), q-c(3) → 6 keywords total
    out = batch_ingest._expand_queries(queries, _tiers_fixture(), tier_override="tier_2")
    # 2 locations in tier_2 × (2+1+3) keywords = 12
    assert len(out) == 2 * (2 + 1 + 3)
    assert all(loc in {"SF, CA", "Seattle, WA"} for _, loc in out)


def test_expand_queries_disable_locations_flattens_to_one_per_keyword() -> None:
    """--no-locations drops geo but still iterates keywords (one call per kw)."""
    queries = _queries_fixture()
    out = batch_ingest._expand_queries(queries, _tiers_fixture(), disable_locations=True)
    total_keywords = sum(len(q["keywords"]) for q in queries)
    assert len(out) == total_keywords
    assert all(loc == "" for _, loc in out)
    # Each emitted entry carries exactly one keyword
    for q, _ in out:
        assert len(q["keywords"]) == 1


def test_expand_queries_empty_keywords_falls_back_to_jobs() -> None:
    """A malformed query with no keywords still emits one call per location."""
    queries = [{"name": "q-empty", "keywords": [], "location_tier": "tier_1"}]
    out = batch_ingest._expand_queries(queries, _tiers_fixture())
    assert len(out) == 2  # 2 tier_1 locations
    assert all(q["keywords"] == ["jobs"] for q, _ in out)


def test_build_region_config_includes_keyword_in_region_id() -> None:
    """region_id encodes name + keyword + location so runs are distinguishable."""
    q = {"name": "ai-engineering", "keywords": ["AI engineer"]}
    region = batch_ingest._build_region_config(q, "El Paso, TX")
    assert region["query_location"] == "El Paso, TX"
    assert region["keywords"] == ["AI engineer"]
    assert "ai-engineer" in region["region_id"]
    assert "el-paso-tx" in region["region_id"]
    assert region["sources"] == ["jsearch"]
    # Hard cap at 100 chars (PostgreSQL column constraint)
    assert len(region["region_id"]) <= 100


def test_build_region_config_empty_location_stays_national() -> None:
    q = {"name": "healthcare-it", "keywords": ["EHR analyst"]}
    region = batch_ingest._build_region_config(q, "")
    assert region["query_location"] == ""
    assert region["region_id"].endswith("national")


def test_propagate_throttle_env_sets_rps_rpm(monkeypatch) -> None:
    monkeypatch.delenv("JSEARCH_RPS", raising=False)
    monkeypatch.delenv("JSEARCH_RPM", raising=False)
    batch_ingest._propagate_throttle_env({"requests_per_second": 5, "requests_per_minute": 250})
    assert os.environ["JSEARCH_RPS"] == "5"
    assert os.environ["JSEARCH_RPM"] == "250"


def test_propagate_throttle_env_no_op_on_empty(monkeypatch) -> None:
    monkeypatch.delenv("JSEARCH_RPS", raising=False)
    monkeypatch.delenv("JSEARCH_RPM", raising=False)
    batch_ingest._propagate_throttle_env({})
    assert "JSEARCH_RPS" not in os.environ
    assert "JSEARCH_RPM" not in os.environ
