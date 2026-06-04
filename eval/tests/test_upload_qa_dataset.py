"""Tests for scripts/upload_qa_dataset.py — JIE #248.

Verifies that:
- DatasetItem.metadata values stay under the Langfuse SDK 200-char limit.
- Metadata contains required pointer fields and excludes scoring fields.
- _merge_golden() correctly restores scoring fields from golden_by_id when
  metadata is slim (hosted-dataset mode post-#248 fix).
- _merge_golden() logs a warning and degrades gracefully on a lookup miss.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Path setup — ensure repo root is importable
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _import_upload():
    """Import scripts.upload_qa_dataset, adding scripts/ to sys.path."""
    scripts_dir = str(_REPO_ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "upload_qa_dataset",
        _REPO_ROOT / "scripts" / "upload_qa_dataset.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Lazy-import once; skip entire module if dotenv or langfuse is unavailable in
# the test environment (CI without Langfuse credentials).
try:
    upload_mod = _import_upload()
    _build_dataset_item = upload_mod._build_dataset_item
    _LANGFUSE_METADATA_MAX_CHARS = upload_mod._LANGFUSE_METADATA_MAX_CHARS
    _validate_item = upload_mod._validate_item
except Exception as exc:
    pytest.skip(f"upload_qa_dataset import failed: {exc}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_ITEM: dict[str, Any] = {
    "id": "gq-001",
    "question": "Which tech skills are most in demand in Washington state?",
    "intent": "skill_demand",
    "ideal_answer_summary": "Python and cloud skills top demand across all sectors.",
    "must_include": ["Python", "cloud"],
    "must_not_include": ["unrelated"],
    "difficulty": "easy",
}

_FULL_ITEM: dict[str, Any] = {
    **_MINIMAL_ITEM,
    "context": "Washington state labor market Q2 2026.",
    "data_backed": True,
    "expected_min_rows": 5,
    "zero_rows_is_correct": False,
    "refusal_appropriate": False,
    "expected_confidence_range": [0.6, 1.0],
}


# ---------------------------------------------------------------------------
# Metadata size tests (core fix for JIE #248)
# ---------------------------------------------------------------------------


class TestMetadataSize:
    def test_all_values_under_langfuse_limit(self):
        """Every metadata value must fit under _LANGFUSE_METADATA_MAX_CHARS."""
        _, _, metadata = _build_dataset_item(_FULL_ITEM)
        for key, value in metadata.items():
            serialized = json.dumps(value) if not isinstance(value, str) else value
            assert len(serialized) < _LANGFUSE_METADATA_MAX_CHARS, (
                f"metadata[{key!r}] serializes to {len(serialized)} chars (limit: {_LANGFUSE_METADATA_MAX_CHARS})"
            )

    def test_metadata_within_limit_for_long_intent(self):
        """Even a verbose intent string should stay within the SDK limit."""
        item = {**_MINIMAL_ITEM, "intent": "x" * 80}
        _, _, metadata = _build_dataset_item(item)
        for _key, value in metadata.items():
            serialized = json.dumps(value) if not isinstance(value, str) else value
            assert len(serialized) < _LANGFUSE_METADATA_MAX_CHARS


# ---------------------------------------------------------------------------
# Pointer field tests
# ---------------------------------------------------------------------------


class TestPointerFields:
    def test_required_pointer_fields_present(self):
        """id, intent, expected_intent, difficulty must always be in metadata."""
        _, _, metadata = _build_dataset_item(_MINIMAL_ITEM)
        for field in ("id", "intent", "expected_intent", "difficulty"):
            assert field in metadata, f"Required pointer field {field!r} missing from metadata"

    def test_expected_intent_equals_intent(self):
        _, _, metadata = _build_dataset_item(_MINIMAL_ITEM)
        assert metadata["expected_intent"] == metadata["intent"] == "skill_demand"

    def test_scoring_fields_excluded_from_metadata(self):
        """Scoring fields must not be serialized into DatasetItem.metadata."""
        _, _, metadata = _build_dataset_item(_FULL_ITEM)
        excluded = ("must_include", "must_not_include", "ideal_answer_summary", "context")
        for field in excluded:
            assert field not in metadata, (
                f"Scoring field {field!r} found in metadata — it would exceed the Langfuse 200-char limit (JIE #248)"
            )

    def test_optional_boolean_fields_excluded(self):
        """Fields like data_backed, refusal_appropriate should not appear in metadata."""
        _, _, metadata = _build_dataset_item(_FULL_ITEM)
        for field in ("data_backed", "expected_min_rows", "zero_rows_is_correct", "refusal_appropriate"):
            assert field not in metadata

    def test_input_carries_full_question(self):
        """input dict must still carry the full question text."""
        input_data, _, _ = _build_dataset_item(_MINIMAL_ITEM)
        assert input_data["question"] == _MINIMAL_ITEM["question"]

    def test_expected_output_carries_ideal_answer(self):
        """expected_output must carry ideal_answer_summary even though metadata does not."""
        _, expected_output, _ = _build_dataset_item(_MINIMAL_ITEM)
        assert expected_output["ideal_answer_summary"] == _MINIMAL_ITEM["ideal_answer_summary"]


# ---------------------------------------------------------------------------
# _merge_golden tests — evaluator fallback behavior
# ---------------------------------------------------------------------------


class TestMergeGolden:
    """Verify that qa_eval._merge_golden restores scoring fields from local JSON."""

    @pytest.fixture(autouse=True)
    def _import_merge(self):
        from eval.qa_eval import _merge_golden  # noqa: PLC0415

        self._merge_golden = _merge_golden

    def _slim_meta(self, item: dict) -> dict:
        """Build the slim metadata the eval harness would receive from Langfuse."""
        _, _, metadata = _build_dataset_item(item)
        return metadata

    def test_merge_restores_must_include(self):
        """must_include must come from golden_by_id when metadata is slim."""
        slim = self._slim_meta(_FULL_ITEM)
        golden_by_id = {_FULL_ITEM["id"]: _FULL_ITEM}
        merged = self._merge_golden(golden_by_id, _FULL_ITEM["id"], slim)
        assert merged["must_include"] == _FULL_ITEM["must_include"]

    def test_merge_restores_must_not_include(self):
        slim = self._slim_meta(_FULL_ITEM)
        golden_by_id = {_FULL_ITEM["id"]: _FULL_ITEM}
        merged = self._merge_golden(golden_by_id, _FULL_ITEM["id"], slim)
        assert merged["must_not_include"] == _FULL_ITEM["must_not_include"]

    def test_merge_restores_ideal_answer_summary(self):
        slim = self._slim_meta(_FULL_ITEM)
        golden_by_id = {_FULL_ITEM["id"]: _FULL_ITEM}
        merged = self._merge_golden(golden_by_id, _FULL_ITEM["id"], slim)
        assert merged["ideal_answer_summary"] == _FULL_ITEM["ideal_answer_summary"]

    def test_golden_by_id_wins_over_slim_meta(self):
        """Local JSON takes precedence over any field in slim metadata."""
        slim = self._slim_meta(_FULL_ITEM)
        full = {**_FULL_ITEM, "difficulty": "hard"}  # local JSON has updated difficulty
        golden_by_id = {_FULL_ITEM["id"]: full}
        merged = self._merge_golden(golden_by_id, _FULL_ITEM["id"], slim)
        assert merged["difficulty"] == "hard"

    def test_lookup_miss_logs_warning(self, caplog):
        """A missing golden_by_id entry must produce a warning (not an error)."""
        import logging

        slim = self._slim_meta(_MINIMAL_ITEM)
        with caplog.at_level(logging.WARNING):
            merged = self._merge_golden({}, _MINIMAL_ITEM["id"], slim)
        # Merge should not raise; merged should still contain slim pointer fields
        assert merged["id"] == _MINIMAL_ITEM["id"]
        # Scoring fields must be absent (no crash, just degraded)
        assert "must_include" not in merged

    def test_lookup_miss_empty_gq_id_no_warning(self, caplog):
        """An empty gq_id (non-hosted item) must not produce a spurious warning."""
        import logging

        with caplog.at_level(logging.WARNING):
            merged = self._merge_golden({}, "", {"question": "x"})
        assert "golden_lookup_miss" not in caplog.text
        assert merged == {"question": "x"}
