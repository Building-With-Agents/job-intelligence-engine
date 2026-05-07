"""Tests for the Ingestion Agent deduplicator."""

from __future__ import annotations

from unittest.mock import MagicMock

from ingestion.deduplicator import (
    compute_fingerprint,
    compute_storage_hash,
    deduplicate_batch,
)


class TestComputeFingerprint:
    """Test fingerprint computation."""

    def test_deterministic(self) -> None:
        """Same input always produces same fingerprint."""
        record = {
            "source": "jsearch",
            "external_id": "123",
            "title": "Engineer",
            "company": "Acme",
            "date_posted": "2026-01-01",
        }
        assert compute_fingerprint(record) == compute_fingerprint(record)

    def test_case_insensitive(self) -> None:
        """Fingerprint is case-insensitive."""
        r1 = {
            "source": "jsearch",
            "external_id": "123",
            "title": "Engineer",
            "company": "Acme",
            "date_posted": "2026-01-01",
        }
        r2 = {
            "source": "JSEARCH",
            "external_id": "123",
            "title": "ENGINEER",
            "company": "ACME",
            "date_posted": "2026-01-01",
        }
        assert compute_fingerprint(r1) == compute_fingerprint(r2)

    def test_whitespace_normalized(self) -> None:
        """Leading/trailing whitespace is stripped."""
        r1 = {
            "source": "jsearch",
            "external_id": "123",
            "title": "Engineer",
            "company": "Acme",
            "date_posted": "2026-01-01",
        }
        r2 = {
            "source": " jsearch ",
            "external_id": " 123 ",
            "title": " Engineer ",
            "company": " Acme ",
            "date_posted": " 2026-01-01 ",
        }
        assert compute_fingerprint(r1) == compute_fingerprint(r2)

    def test_different_inputs_diverge(self) -> None:
        """Different records produce different fingerprints."""
        r1 = {
            "source": "jsearch",
            "external_id": "123",
            "title": "Engineer",
            "company": "Acme",
            "date_posted": "2026-01-01",
        }
        r2 = {
            "source": "jsearch",
            "external_id": "456",
            "title": "Manager",
            "company": "Acme",
            "date_posted": "2026-01-01",
        }
        assert compute_fingerprint(r1) != compute_fingerprint(r2)

    def test_jie209_date_posted_excluded(self) -> None:
        """JIE#209 regression: shifting ``date_posted`` does NOT change the fingerprint
        or the storage hash. JSearch mutates ``job_posted_at_datetime_utc`` between
        re-fetches for the same job; if it were in the hash, re-fetches would bypass
        the ``uq_raw_ingested_jobs_hash`` unique constraint.
        """
        base = {
            "source": "jsearch",
            "external_id": "abc123",
            "title": "Software Engineer",
            "company": "Acme Corp",
            "date_posted": "2026-04-01",
        }
        shifted = dict(base, date_posted="2026-04-15")  # later re-fetch
        also_shifted = dict(base, date_posted=None)  # missing entirely

        assert compute_fingerprint(base) == compute_fingerprint(shifted)
        assert compute_fingerprint(base) == compute_fingerprint(also_shifted)
        assert compute_storage_hash(base) == compute_storage_hash(shifted)
        assert compute_storage_hash(base) == compute_storage_hash(also_shifted)

    def test_jie209_canonical_hash_snapshot(self) -> None:
        """Pin the hash literals so future hash-input changes are caught.

        If you intentionally change what's hashed (e.g. add ``location`` to the
        fingerprint), update these literals in the same commit and document the
        rationale — the cross-batch dedup constraint depends on stable hashes.
        """
        canonical = {
            "source": "jsearch",
            "external_id": "abc123",
            "title": "Software Engineer",
            "company": "Acme Corp",
            "date_posted": "2026-04-01",
        }
        assert compute_fingerprint(canonical) == "96d5a3fe9c0256a0ae465324f4531452cf6e4ff3fc8fde9c9a2a534cb49f1ea0"
        assert compute_storage_hash(canonical) == "49badcdb24b7d9f066e90c69f87428a5de9b9eca1533250290e5e824a81ae146"


class TestDeduplicateBatch:
    """Test batch deduplication."""

    def _mock_session(self, existing_hashes: list[str] | None = None) -> MagicMock:
        """Create a mock session that returns specified existing hashes."""
        session = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = existing_hashes or []
        execute_mock = MagicMock()
        execute_mock.scalars.return_value = scalars_mock
        session.execute.return_value = execute_mock
        return session

    def test_in_batch_dedup(self) -> None:
        """Duplicate records within a batch are deduplicated."""
        records = [
            {"source": "jsearch", "external_id": "1", "title": "Dev", "company": "A", "date_posted": "2026-01-01"},
            {"source": "jsearch", "external_id": "1", "title": "Dev", "company": "A", "date_posted": "2026-01-01"},
        ]
        result = deduplicate_batch(records, self._mock_session())
        assert len(result.new_records) == 1
        assert result.duplicates_skipped >= 1

    def test_source_priority_jsearch_wins(self) -> None:
        """JSearch wins over crawl4ai when same job appears in both (IC #9)."""
        records = [
            {"source": "crawl4ai", "external_id": "1", "title": "Dev", "company": "A", "date_posted": "2026-01-01"},
            {"source": "jsearch", "external_id": "1", "title": "Dev", "company": "A", "date_posted": "2026-01-01"},
        ]
        result = deduplicate_batch(records, self._mock_session())
        assert len(result.new_records) == 1
        assert result.new_records[0]["source"] == "jsearch"
        assert result.source_priority_resolved >= 1

    def test_cross_batch_dedup(self) -> None:
        """Records already in the DB are skipped."""
        record = {"source": "jsearch", "external_id": "1", "title": "Dev", "company": "A", "date_posted": "2026-01-01"}
        storage_hash = compute_storage_hash(record)
        session = self._mock_session(existing_hashes=[storage_hash])
        result = deduplicate_batch([record], session)
        assert len(result.new_records) == 0
        assert result.duplicates_skipped == 1

    def test_unique_records_pass_through(self) -> None:
        """Unique records are all preserved."""
        records = [
            {"source": "jsearch", "external_id": "1", "title": "Dev", "company": "A", "date_posted": "2026-01-01"},
            {"source": "jsearch", "external_id": "2", "title": "PM", "company": "B", "date_posted": "2026-01-02"},
        ]
        result = deduplicate_batch(records, self._mock_session())
        assert len(result.new_records) == 2
        assert result.duplicates_skipped == 0
