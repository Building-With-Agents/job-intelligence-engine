"""Unit tests for batch_ingest.py helpers (issue #157).

Covers:
- `_expand_queries` cartesian-product expansion by `location_tier`
- Unknown / null tier fallback behavior
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
        {"name": "q-c", "keywords": ["c1"], "location_tier": None},
        {"name": "q-d", "keywords": ["d1"], "location_tier": "does_not_exist"},
        {"name": "q-e", "keywords": ["e1"]},  # no key at all
    ]


def _tiers_fixture() -> dict[str, list[str]]:
    return {
        "tier_1": ["El Paso, TX", "Las Cruces, NM", "Ciudad Juarez, Chihuahua, Mexico"],
        "tier_2": ["SF, CA", "Seattle, WA"],
    }


def test_expand_queries_cartesian_for_tagged_queries() -> None:
    queries = _queries_fixture()[:2]  # only q-a (tier_1=3) and q-b (tier_2=2)
    out = batch_ingest._expand_queries(queries, _tiers_fixture())
    assert len(out) == 3 + 2
    # q-a fans out to all three tier_1 locations in order
    assert [loc for q, loc in out if q["name"] == "q-a"] == [
        "El Paso, TX",
        "Las Cruces, NM",
        "Ciudad Juarez, Chihuahua, Mexico",
    ]
    # q-b fans out to two tier_2 locations
    assert [loc for q, loc in out if q["name"] == "q-b"] == ["SF, CA", "Seattle, WA"]


def test_expand_queries_null_tier_runs_once() -> None:
    queries = _queries_fixture()
    out = batch_ingest._expand_queries(queries, _tiers_fixture())
    # q-c (null tier), q-d (unknown tier), q-e (no tier key) all emit one empty-location entry
    single_slots = [(q["name"], loc) for q, loc in out if q["name"] in {"q-c", "q-d", "q-e"}]
    assert single_slots == [("q-c", ""), ("q-d", ""), ("q-e", "")]


def test_expand_queries_tier_override_applies_to_all() -> None:
    queries = _queries_fixture()[:3]  # q-a, q-b, q-c
    out = batch_ingest._expand_queries(
        queries, _tiers_fixture(), tier_override="tier_2"
    )
    # Every query now uses tier_2 (2 locations each) regardless of its YAML tag
    assert len(out) == 3 * 2
    assert all(loc in {"SF, CA", "Seattle, WA"} for _, loc in out)


def test_expand_queries_disable_locations_flattens_to_one_each() -> None:
    queries = _queries_fixture()
    out = batch_ingest._expand_queries(
        queries, _tiers_fixture(), disable_locations=True
    )
    assert len(out) == len(queries)
    assert all(loc == "" for _, loc in out)


def test_build_region_config_sets_query_location() -> None:
    q = {"name": "ai-engineering", "keywords": ["AI engineer"]}
    region = batch_ingest._build_region_config(q, "El Paso, TX")
    assert region["query_location"] == "El Paso, TX"
    assert region["keywords"] == ["AI engineer"]
    assert "el-paso-tx" in region["region_id"]
    assert region["sources"] == ["jsearch"]


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
