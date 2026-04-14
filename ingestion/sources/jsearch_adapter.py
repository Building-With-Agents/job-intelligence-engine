"""JSearch source adapter - fetches job postings via RapidAPI JSearch (httpx).

API key from environment: JSEARCH_API_KEY. No hardcoded credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime

import httpx
import structlog

from common.types.raw_job_record import RawJobRecord
from common.types.region_config import RegionConfig
from ingestion.sources.base_adapter import SourceAdapter

JSEARCH_BASE_URL = "https://jsearch.p.rapidapi.com/search"
JSEARCH_HOST = "jsearch.p.rapidapi.com"
log = structlog.get_logger()


def _fingerprint(source: str, external_id: str, title: str, company: str, date_posted: str) -> str:
    """SHA-256 fingerprint for dedup: source + external_id + title + company + date_posted."""
    payload = f"{source}|{external_id}|{title}|{company}|{date_posted}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_date(value: str | int | float | None) -> datetime | None:
    """Parse JSearch date (often Unix timestamp or ISO string) to datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(value)
        except (OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _env_int(name: str, default: int) -> int:
    """Parse a positive integer env var with a safe fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _retry_delay_seconds(retry_after: str | None, attempt: int, base_delay: int, max_delay: int) -> int:
    """Resolve retry delay from Retry-After or exponential backoff."""
    if retry_after:
        try:
            return max(1, min(max_delay, int(float(retry_after))))
        except (TypeError, ValueError):
            pass
    return min(max_delay, base_delay * (2 ** attempt))


def _job_to_raw_record(job: dict, region_id: str) -> RawJobRecord:
    """Map a single JSearch API job object to RawJobRecord."""
    # JSearch often uses employer_name, job_title, job_id, job_apply_link, job_city, job_state, job_country
    external_id = str(job.get("job_id") or job.get("id") or "")
    if not external_id:
        external_id = hashlib.sha256(str(job).encode("utf-8")).hexdigest()[:32]

    title = (job.get("job_title") or job.get("title") or "").strip() or "Untitled"
    company = (job.get("employer_name") or job.get("company_name") or job.get("company") or "").strip() or "Unknown"
    description = job.get("job_description") or job.get("description") or job.get("job_highlights") or ""
    if isinstance(description, dict):
        description = " ".join(
            str(v)
            for v in (description.get("Qualifications", []) or []) + (description.get("Responsibilities", []) or [])
        )
    if not isinstance(description, str):
        description = str(description or "")

    date_posted_val = (
        job.get("job_posted_at_timestamp") or job.get("job_posted_at_datetime_utc") or job.get("posted_at")
    )
    date_posted = _parse_date(date_posted_val)

    job_url = job.get("job_apply_link") or job.get("job_google_link") or job.get("apply_link") or None
    if job_url and not isinstance(job_url, str):
        job_url = str(job_url) if job_url else None

    # Location
    city = job.get("job_city")
    state = job.get("job_state") or job.get("job_state_code")
    country = job.get("job_country")
    zip_code = job.get("job_zip_code") or job.get("job_postal_code")
    is_remote = job.get("job_is_remote")
    if is_remote is not None and not isinstance(is_remote, bool):
        is_remote = str(is_remote).lower() in ("true", "1", "yes")

    # Salary — JSearch may have job_min_salary, job_max_salary, job_salary_currency, job_salary_period
    salary_min = job.get("job_min_salary") or job.get("min_salary")
    salary_max = job.get("job_max_salary") or job.get("max_salary")
    if salary_min is not None and not isinstance(salary_min, (int, float)):
        try:
            salary_min = float(salary_min)
        except (TypeError, ValueError):
            salary_min = None
    if salary_max is not None and not isinstance(salary_max, (int, float)):
        try:
            salary_max = float(salary_max)
        except (TypeError, ValueError):
            salary_max = None
    salary_currency = job.get("job_salary_currency") or job.get("salary_currency")
    salary_period = job.get("job_salary_period") or job.get("salary_period")
    salary_raw = job.get("job_salary") or job.get("job_salary_display")

    employment_type = job.get("job_employment_type") or job.get("employment_type")
    experience_level = (
        job.get("job_required_experience", {}).get("required_experience_level")
        if isinstance(job.get("job_required_experience"), dict)
        else job.get("experience_level")
    )

    date_posted_str = date_posted.isoformat() if date_posted else ""
    raw_payload_hash = _fingerprint("jsearch", external_id, title, company, date_posted_str)

    return RawJobRecord(
        external_id=external_id,
        source="jsearch",
        region_id=region_id,
        raw_payload_hash=raw_payload_hash,
        title=title,
        company=company,
        description=description[:50000] if description else "",
        city=city[:255] if isinstance(city, str) else None,
        state=state[:100] if isinstance(state, str) else None,
        country=country[:10] if isinstance(country, str) else None,
        zip_code=str(zip_code)[:10] if zip_code else None,
        is_remote=is_remote,
        date_posted=date_posted,
        salary_raw=str(salary_raw)[:255] if salary_raw is not None else None,
        salary_min=float(salary_min) if salary_min is not None else None,
        salary_max=float(salary_max) if salary_max is not None else None,
        salary_currency=str(salary_currency)[:10] if salary_currency else None,
        salary_period=str(salary_period)[:20] if salary_period else None,
        employment_type=str(employment_type)[:50] if employment_type else None,
        experience_level=str(experience_level)[:50] if experience_level else None,
        job_url=job_url[:2083] if job_url else None,
        source_url=JSEARCH_BASE_URL,
        raw_payload=dict(job),
    )


class JSearchAdapter(SourceAdapter):
    """SourceAdapter implementation for JSearch API (RapidAPI)."""

    @property
    def source_name(self) -> str:
        return "jsearch"

    async def fetch(self, region: RegionConfig) -> list[RawJobRecord]:
        """Fetch raw job postings from JSearch for the given region."""
        api_key = os.getenv("JSEARCH_API_KEY")
        if not api_key:
            raise ValueError("JSEARCH_API_KEY is not set")

        # Build query from region: location + keywords/role_categories
        query_parts = [region.query_location] if region.query_location else []
        if region.keywords:
            query_parts.extend(region.keywords[:3])
        if region.role_categories:
            query_parts.extend(region.role_categories[:2])
        query = " ".join(query_parts).strip() or "jobs"

        num_pages = max(1, min(50, _env_int("JSEARCH_MAX_PAGES", 50)))
        max_retries = max(0, _env_int("JSEARCH_MAX_RETRIES", 2))
        base_delay = max(1, _env_int("JSEARCH_RETRY_BASE_DELAY_SECONDS", 10))
        max_delay = max(base_delay, _env_int("JSEARCH_RETRY_MAX_DELAY_SECONDS", 60))

        all_records: list[RawJobRecord] = []
        seen_hashes: set[str] = set()
        async with httpx.AsyncClient(timeout=30.0) as client:
            for page in range(1, num_pages + 1):
                attempt = 0
                while True:
                    response = await client.get(
                        JSEARCH_BASE_URL,
                        params={
                            "query": query,
                            "page": str(page),
                            "num_pages": "1",
                        },
                        headers={
                            "X-RapidAPI-Key": api_key,
                            "X-RapidAPI-Host": JSEARCH_HOST,
                        },
                    )
                    try:
                        response.raise_for_status()
                        break
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code != 429 or attempt >= max_retries:
                            raise
                        delay_s = _retry_delay_seconds(
                            exc.response.headers.get("Retry-After"),
                            attempt,
                            base_delay,
                            max_delay,
                        )
                        log.warning(
                            "jsearch_rate_limited",
                            query=query,
                            page=page,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            delay_s=delay_s,
                        )
                        attempt += 1
                        await asyncio.sleep(delay_s)
                data = response.json()
                jobs = data.get("data") if isinstance(data, dict) else []
                if not jobs:
                    break
                for job in jobs:
                    if isinstance(job, dict):
                        rec = _job_to_raw_record(job, region.region_id)
                        if rec.raw_payload_hash and rec.raw_payload_hash not in seen_hashes:
                            seen_hashes.add(rec.raw_payload_hash)
                            all_records.append(rec)
                if len(jobs) < 10:
                    break

        return all_records

    async def health_check(self) -> dict:
        """Return reachable status; if no API key, return reachable: False."""
        if not os.getenv("JSEARCH_API_KEY"):
            return {"reachable": False, "source": "jsearch"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    JSEARCH_BASE_URL,
                    params={"query": "test", "page": "1", "num_pages": "1"},
                    headers={
                        "X-RapidAPI-Key": os.getenv("JSEARCH_API_KEY", ""),
                        "X-RapidAPI-Host": JSEARCH_HOST,
                    },
                )
                return {"reachable": r.status_code == 200, "source": "jsearch"}
        except Exception:
            return {"reachable": False, "source": "jsearch"}
