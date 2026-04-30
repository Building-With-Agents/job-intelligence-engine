"""Field mapper for records sourced from JSearch API.

Zip-code resolution: prefer the raw zip if present; otherwise, when city +
state are available, look up postal_geo_data by city + 2-letter state code.
JSearch sends `state` as either a 2-letter code (``"TX"``) or a full name
(``"Texas"``); the prior implementation truncated full names to two letters
(``"TE"``) and silently failed every lookup. This mapper normalizes both
forms before querying.
"""

from __future__ import annotations

import structlog

from common.types.job_record import JobRecord
from common.types.raw_job_record import RawJobRecord
from normalization.cleaners import (
    clean_text,
    normalize_date,
    normalize_employment_type,
    parse_salary,
)
from normalization.mappers.base import MapperBase

log = structlog.get_logger()


_STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
    "puerto rico": "PR",
}


def _normalize_state_to_code(state: str | None) -> str | None:
    """Accept a 2-letter state code or a full state name; return the code."""
    if not state:
        return None
    cleaned = state.strip()
    if not cleaned:
        return None
    if len(cleaned) == 2:
        return cleaned.upper()
    return _STATE_NAME_TO_CODE.get(cleaned.lower())


def _resolve_zip_code(city: str | None, state: str | None, raw_zip: str | None) -> str | None:
    """Resolve zip code: prefer the raw value; else look up postal_geo_data."""
    if raw_zip:
        return raw_zip.strip()[:10] or None

    if not city or not state:
        return None

    state_code = _normalize_state_to_code(state)
    if not state_code:
        log.warning("zip_resolution_unknown_state", city=city, state=state)
        return None

    try:
        from common.data_store.database import check_db_connection, session_scope
        from common.data_store.models import PostalGeoData

        if not check_db_connection():
            return None

        city_clean = city.strip().lower()

        with session_scope() as session:
            match = (
                session.query(PostalGeoData.zip)
                .filter(
                    PostalGeoData.state_code == state_code,
                    PostalGeoData.city.ilike(city_clean),
                )
                .first()
            )
            if match:
                return match.zip
            log.warning(
                "zip_resolution_no_match",
                city=city,
                state=state,
                state_code=state_code,
            )
    except Exception as exc:
        log.warning(
            "zip_resolution_failed",
            city=city,
            state=state,
            error=str(exc),
        )

    return None


class JSearchMapper(MapperBase):
    """Map JSearch raw fields to canonical normalized fields.

    Applies cleaners to produce a validated ``JobRecord``.
    """

    @property
    def mapper_name(self) -> str:
        return "jsearch_mapper"

    def map(self, raw: RawJobRecord) -> JobRecord:
        # Parse salary from raw string if structured salary fields are absent
        salary_data = {}
        if raw.salary_min is not None or raw.salary_max is not None:
            salary_data = {
                "salary_min": raw.salary_min,
                "salary_max": raw.salary_max,
                "salary_currency": raw.salary_currency or "USD",
                "salary_period": raw.salary_period or "annual",
            }
        elif raw.salary_raw:
            salary_data = parse_salary(raw.salary_raw)

        zip_code = _resolve_zip_code(raw.city, raw.state, raw.zip_code)

        return JobRecord(
            source=raw.source,
            external_id=raw.external_id,
            region_id=raw.region_id,
            title=clean_text(raw.title),
            company=clean_text(raw.company),
            description=clean_text(raw.description),
            job_url=raw.job_url,
            city=raw.city,
            state_province=raw.state,
            country=raw.country,
            zip_code=zip_code,
            is_remote=raw.is_remote,
            date_posted=normalize_date(raw.date_posted.isoformat() if raw.date_posted else None),
            salary_raw=raw.salary_raw,
            salary_min=salary_data.get("salary_min"),
            salary_max=salary_data.get("salary_max"),
            salary_currency=salary_data.get("salary_currency"),
            salary_period=salary_data.get("salary_period"),
            employment_type=normalize_employment_type(raw.employment_type),
            experience_level=raw.experience_level,
            mapper_used=self.mapper_name,
        )
