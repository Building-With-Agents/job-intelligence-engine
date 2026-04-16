"""Tests for the JSearch source adapter (all mocked — no real API calls)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

from common.types.region_config import RegionConfig
from ingestion.sources.jsearch_adapter import JSearchAdapter, _job_to_raw_record

_TEST_REGION = RegionConfig(
    region_id="test-region",
    display_name="Test",
    query_location="Test City",
    radius_miles=50,
    states=["WA"],
    countries=["US"],
    sources=["jsearch"],
    role_categories=["Software Engineering"],
    keywords=["software engineer"],
)


class TestJSearchFieldMapping:
    """Test field mapping from JSearch API response to canonical RawJobRecord."""

    def test_map_complete_record(self) -> None:
        raw = {
            "job_id": "abc123",
            "job_title": "Software Engineer",
            "employer_name": "Acme Corp",
            "job_city": "Seattle",
            "job_state": "WA",
            "job_description": "Build things.",
            "job_apply_link": "https://acme.com/apply",
            "job_posted_at_datetime_utc": "2026-01-15T00:00:00Z",
        }
        result = _job_to_raw_record(raw, "test-region")
        assert result.external_id == "abc123"
        assert result.source == "jsearch"
        assert result.title == "Software Engineer"
        assert result.company == "Acme Corp"
        assert result.city == "Seattle"
        assert result.state == "WA"
        assert result.description == "Build things."
        assert result.job_url == "https://acme.com/apply"

    def test_map_missing_city(self) -> None:
        raw = {"job_id": "x", "job_title": "Dev", "employer_name": "Co", "job_state": "WA"}
        result = _job_to_raw_record(raw, "test-region")
        assert result.city is None
        assert result.state == "WA"

    def test_map_empty_response(self) -> None:
        result = _job_to_raw_record({}, "test-region")
        assert result.source == "jsearch"
        assert result.title == "Untitled"
        assert result.company == "Unknown"


class TestJSearchAdapter:
    """Test adapter behavior."""

    def test_no_api_key_raises(self) -> None:
        """Without JSEARCH_API_KEY, fetch raises ValueError."""
        import pytest

        with patch.dict(os.environ, {"JSEARCH_API_KEY": ""}, clear=False):
            adapter = JSearchAdapter()
            with pytest.raises(ValueError):
                asyncio.run(adapter.fetch(region=_TEST_REGION))

    def test_health_check_without_key(self) -> None:
        with patch.dict(os.environ, {"JSEARCH_API_KEY": ""}, clear=False):
            adapter = JSearchAdapter()
            result = asyncio.run(adapter.health_check())
            assert result["reachable"] is False


class TestJSearchQueryConstruction:
    """Issue #165 — `"<kw> in <loc>"` format + country/language params."""

    def _mock_response(self):
        from unittest.mock import AsyncMock, MagicMock

        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json = MagicMock(return_value={"data": []})
        mock.status_code = 200
        return AsyncMock(return_value=mock)

    def test_query_uses_role_in_location_format(self) -> None:
        """region.keywords[0] + location → '<kw> in <loc>', with country/language params."""
        from unittest.mock import patch

        from ingestion.sources.jsearch_adapter import _reset_rate_limit_state_for_tests

        _reset_rate_limit_state_for_tests()
        region = RegionConfig(
            region_id="test",
            display_name="t",
            query_location="El Paso, TX",
            radius_miles=50,
            states=[],
            countries=["US"],
            sources=["jsearch"],
            role_categories=[],
            keywords=["AI engineer"],
        )
        captured_params: list[dict] = []

        async def fake_get(self, url, *, params=None, headers=None, **kwargs):
            captured_params.append(params or {})
            resp = type("R", (), {})()
            resp.raise_for_status = lambda: None
            resp.json = lambda: {"data": []}
            resp.status_code = 200
            return resp

        with (
            patch.dict(
                os.environ,
                {"JSEARCH_API_KEY": "k", "JSEARCH_MAX_PAGES": "1", "JSEARCH_RPS": "100"},
                clear=False,
            ),
            patch("httpx.AsyncClient.get", new=fake_get),
        ):
            asyncio.run(JSearchAdapter().fetch(region=region))

        assert captured_params, "adapter never called client.get"
        p = captured_params[0]
        assert p["query"] == "AI engineer in El Paso, TX"
        assert p["country"] == "us"
        assert p["language"] == "en"
        assert p["date_posted"] == "all"

    def test_query_without_location_omits_in_keyword(self) -> None:
        """Empty location → query is just the keyword, not '<kw> in '."""
        from unittest.mock import patch

        from ingestion.sources.jsearch_adapter import _reset_rate_limit_state_for_tests

        _reset_rate_limit_state_for_tests()
        region = RegionConfig(
            region_id="test",
            display_name="t",
            query_location="",
            radius_miles=50,
            states=[],
            countries=["US"],
            sources=["jsearch"],
            role_categories=[],
            keywords=["data scientist"],
        )
        captured: list[dict] = []

        async def fake_get(self, url, *, params=None, headers=None, **kwargs):
            captured.append(params or {})
            resp = type("R", (), {})()
            resp.raise_for_status = lambda: None
            resp.json = lambda: {"data": []}
            resp.status_code = 200
            return resp

        with (
            patch.dict(
                os.environ,
                {"JSEARCH_API_KEY": "k", "JSEARCH_MAX_PAGES": "1", "JSEARCH_RPS": "100"},
                clear=False,
            ),
            patch("httpx.AsyncClient.get", new=fake_get),
        ):
            asyncio.run(JSearchAdapter().fetch(region=region))

        assert captured[0]["query"] == "data scientist"
