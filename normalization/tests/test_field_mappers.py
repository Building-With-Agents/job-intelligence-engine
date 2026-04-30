"""Tests for normalization field mappers."""

from __future__ import annotations

from unittest.mock import patch

from common.types.raw_job_record import RawJobRecord
from normalization.field_mappers.jsearch_mapper import JSearchMapper
from normalization.field_mappers.scraper_mapper import ScraperMapper
from normalization.mappers.jsearch import (
    _normalize_state_to_code,
    _resolve_zip_code,
)


class TestJSearchMapper:
    def test_maps_core_fields(self) -> None:
        raw = RawJobRecord(
            external_id="abc",
            source="jsearch",
            title="Engineer",
            company="Acme",
            description="Job description here.",
            date_posted="2026-01-15T00:00:00Z",
            salary_raw="$120k",
            employment_type="FULLTIME",
        )
        mapper = JSearchMapper()
        result = mapper.map(raw)
        assert result.title == "Engineer"
        assert result.company == "Acme"
        assert result.source == "jsearch"
        assert result.salary_raw == "$120k"
        assert result.employment_type is not None

    def test_missing_payload(self) -> None:
        raw = RawJobRecord(
            external_id="x",
            title="Dev",
            company="Co",
            source="jsearch",
        )
        mapper = JSearchMapper()
        result = mapper.map(raw)
        assert result.salary_min is None
        assert result.salary_raw is None


class TestNormalizeStateToCode:
    def test_two_letter_code_passthrough(self) -> None:
        assert _normalize_state_to_code("TX") == "TX"
        assert _normalize_state_to_code("tx") == "TX"

    def test_full_name_to_code(self) -> None:
        assert _normalize_state_to_code("Texas") == "TX"
        assert _normalize_state_to_code("texas") == "TX"
        assert _normalize_state_to_code("North Carolina") == "NC"
        assert _normalize_state_to_code("New York") == "NY"
        assert _normalize_state_to_code("District of Columbia") == "DC"

    def test_unknown_or_empty(self) -> None:
        assert _normalize_state_to_code(None) is None
        assert _normalize_state_to_code("") is None
        assert _normalize_state_to_code("   ") is None
        assert _normalize_state_to_code("Atlantis") is None


class TestResolveZipCode:
    def test_raw_zip_passthrough(self) -> None:
        assert _resolve_zip_code("El Paso", "TX", "79901") == "79901"

    def test_raw_zip_truncated_to_10(self) -> None:
        assert _resolve_zip_code(None, None, "79901-12345-extra") == "79901-1234"

    def test_missing_city_or_state_returns_none(self) -> None:
        assert _resolve_zip_code(None, "Texas", None) is None
        assert _resolve_zip_code("El Paso", None, None) is None

    def test_full_state_name_normalizes_then_looks_up(self) -> None:
        with (
            patch("common.data_store.database.check_db_connection", return_value=True),
            patch("common.data_store.database.session_scope") as mock_scope,
        ):
            session = mock_scope.return_value.__enter__.return_value
            match = type("M", (), {"zip": "79901"})()
            session.query.return_value.filter.return_value.first.return_value = match

            result = _resolve_zip_code("El Paso", "Texas", None)

            assert result == "79901"
            filter_call = session.query.return_value.filter.call_args
            assert len(filter_call.args) == 2

    def test_unknown_state_returns_none_without_querying(self) -> None:
        with patch("common.data_store.database.check_db_connection") as mock_check:
            result = _resolve_zip_code("El Paso", "Atlantis", None)
            assert result is None
            mock_check.assert_not_called()


class TestJSearchMapperZipResolution:
    def test_map_passes_resolved_zip_to_job_record(self) -> None:
        raw = RawJobRecord(
            external_id="abc",
            source="jsearch",
            title="Engineer",
            company="Acme",
            city="El Paso",
            state="Texas",
        )
        with patch("normalization.mappers.jsearch._resolve_zip_code", return_value="79901") as mock_resolve:
            result = JSearchMapper().map(raw)

        mock_resolve.assert_called_once_with("El Paso", "Texas", None)
        assert result.zip_code == "79901"

    def test_map_when_zip_unresolved_sets_none(self) -> None:
        raw = RawJobRecord(
            external_id="abc",
            source="jsearch",
            title="Engineer",
            company="Acme",
            city="Unknown",
            state="Atlantis",
        )
        with patch("normalization.mappers.jsearch._resolve_zip_code", return_value=None):
            result = JSearchMapper().map(raw)
        assert result.zip_code is None


class TestScraperMapper:
    def test_maps_core_fields(self) -> None:
        raw = RawJobRecord(
            external_id="1",
            source="crawl4ai",
            title="Data Scientist",
            company="BigCo",
            description="Great job.",
            date_posted="2026-02-24T08:15:00Z",
        )
        mapper = ScraperMapper()
        result = mapper.map(raw)
        assert result.title == "Data Scientist"
        assert result.source == "crawl4ai"

    def test_handles_missing_fields(self) -> None:
        raw = RawJobRecord(
            external_id="empty",
            source="crawl4ai",
            title="Untitled",
            company="Unknown",
        )
        mapper = ScraperMapper()
        result = mapper.map(raw)
        assert result.title == "Untitled"
        assert result.source == "crawl4ai"
