"""Upload Q&A golden questions to Langfuse as a dataset.

Creates (or updates) the "LaborPulse Golden Questions" Langfuse dataset from
``eval/qa_golden_questions.json``.

The dataset is the **immutable measuring instrument** for all prompt-version
runs in Weeks 10–11.  Upload it once; every subsequent ``qa_eval.py`` run
creates a new *dataset run* against the same items.  Re-uploading with the
same ``--dataset-name`` is safe: each item carries its stable JSON ``id``
(e.g. ``gq-041``) which Langfuse uses as an upsert key, so existing items
are updated in place and existing dataset runs remain aligned.

Dataset item layout
-------------------
``input``          — ``{"question": "..."}`` — what the eval harness POSTs to
                     ``/analytics/query``.
``expected_output`` — ``{"ideal_answer_summary": "..."}`` — the human-written
                     reference used during Layer 2 manual scoring.
``metadata``       — full golden-question record; everything ``qa_eval.py``
                     needs at score time (``expected_intent``, ``must_include``,
                     ``must_not_include``, ``difficulty``, …).

Prerequisites
-------------
- Langfuse running at ``LANGFUSE_BASE_URL`` (self-hosted Docker on port 3001,
  or cloud).
- ``LANGFUSE_SECRET_KEY`` and ``LANGFUSE_PUBLIC_KEY`` set in ``.env``.

Usage
-----
    python scripts/upload_qa_dataset.py
    python scripts/upload_qa_dataset.py --dataset-name "My Staging Dataset"
    python scripts/upload_qa_dataset.py --json-path path/to/custom.json
    python scripts/upload_qa_dataset.py --dry-run   # validate JSON; no API calls
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

from langfuse import Langfuse  # noqa: E402

DEFAULT_DATASET_NAME = "LaborPulse Golden Questions"
DEFAULT_JSON_PATH = _REPO_ROOT / "eval" / "qa_golden_questions.json"

# Langfuse SDK hard-codes a 200-char limit on propagated span attributes (JIE #248).
# DatasetItem.metadata must stay under this per-value ceiling to avoid the SDK
# dropping values with the warning:
#   "Propagated attribute 'experiment_item_metadata' value is over 200 characters"
# Scoring fields (must_include, must_not_include, ideal_answer_summary, context)
# are intentionally excluded here; the eval harness reads them from the local
# golden-question JSON via golden_by_id rather than from propagated metadata.
_LANGFUSE_METADATA_MAX_CHARS = 200

_VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard"})
_REQUIRED_FIELDS = (
    "id",
    "question",
    "intent",
    "ideal_answer_summary",
    "must_include",
    "must_not_include",
    "difficulty",
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_item(item: dict, idx: int) -> None:
    """Raise ``ValueError`` if a golden-question record is malformed."""
    for field in _REQUIRED_FIELDS:
        if field not in item:
            raise ValueError(f"Item[{idx}] (id={item.get('id', '<missing>')!r}) is missing required field {field!r}")
    gq_id: str = item["id"]
    if not gq_id.startswith("gq-"):
        raise ValueError(f"Item[{idx}] id must start with 'gq-'; got {gq_id!r}")
    if not isinstance(item["must_include"], list):
        raise ValueError(f"Item[{idx}] {gq_id!r}: 'must_include' must be a list")
    if not isinstance(item["must_not_include"], list):
        raise ValueError(f"Item[{idx}] {gq_id!r}: 'must_not_include' must be a list")
    if item["difficulty"] not in _VALID_DIFFICULTIES:
        raise ValueError(
            f"Item[{idx}] {gq_id!r}: 'difficulty' must be one of "
            f"{sorted(_VALID_DIFFICULTIES)}; got {item['difficulty']!r}"
        )
    if "data_backed" in item and not isinstance(item["data_backed"], bool):
        raise ValueError(f"Item[{idx}] {gq_id!r}: data_backed must be bool if set")
    if "expected_min_rows" in item and item["expected_min_rows"] is not None:
        try:
            int(item["expected_min_rows"])
        except (TypeError, ValueError) as e:
            raise ValueError(f"Item[{idx}] {gq_id!r}: expected_min_rows must be an integer") from e
    if "zero_rows_is_correct" in item and not isinstance(item["zero_rows_is_correct"], bool):
        raise ValueError(f"Item[{idx}] {gq_id!r}: zero_rows_is_correct must be bool if set")
    if "refusal_appropriate" in item and not isinstance(item["refusal_appropriate"], bool):
        raise ValueError(f"Item[{idx}] {gq_id!r}: refusal_appropriate must be bool if set")
    if "expected_confidence_range" in item and item["expected_confidence_range"] is not None:
        ecr = item["expected_confidence_range"]
        if not isinstance(ecr, (list, tuple)) or len(ecr) != 2:
            raise ValueError(f"Item[{idx}] {gq_id!r}: expected_confidence_range must be [lo, hi]")
        try:
            float(ecr[0])
            float(ecr[1])
        except (TypeError, ValueError) as e:
            raise ValueError(f"Item[{idx}] {gq_id!r}: expected_confidence_range bounds must be numbers") from e


def _validate_no_duplicate_ids(questions: list[dict]) -> None:
    seen: dict[str, int] = {}
    for idx, item in enumerate(questions):
        gq_id = item.get("id", "")
        if gq_id in seen:
            raise ValueError(f"Duplicate id {gq_id!r} at index {idx} (first seen at index {seen[gq_id]})")
        seen[gq_id] = idx


# ---------------------------------------------------------------------------
# Dataset item builder
# ---------------------------------------------------------------------------


def _build_dataset_item(item: dict) -> tuple[dict, dict, dict]:
    """Return ``(input, expected_output, metadata)`` for one golden question.

    ``metadata`` contains only stable pointer fields so every value stays under
    the Langfuse SDK 200-char propagation limit (JIE #248).  Scoring fields
    (``must_include``, ``must_not_include``, ``ideal_answer_summary``, etc.)
    are intentionally omitted; ``qa_eval.py`` resolves them from the local
    golden-question JSON via ``golden_by_id`` and ``_merge_golden()``.

    ``expected_intent`` is the canonical key the eval harness reads for
    ``intent_accuracy`` scoring; ``intent`` is kept alongside it for
    readability in the Langfuse UI.
    """
    input_data = {"question": item["question"]}
    expected_output = {"ideal_answer_summary": item["ideal_answer_summary"]}
    metadata: dict = {
        "id": item["id"],
        "intent": item["intent"],
        "expected_intent": item["intent"],
        "difficulty": item["difficulty"],
    }
    return input_data, expected_output, metadata


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def upload(
    questions: list[dict],
    *,
    dataset_name: str,
    dry_run: bool,
) -> None:
    client: Langfuse | None = None if dry_run else Langfuse(timeout=60)

    if client is not None:
        # create_dataset is idempotent — safe to call on every upload.
        client.create_dataset(name=dataset_name)
        log.info("Dataset : %s", dataset_name)

    ok = 0
    err = 0

    for item in questions:
        gq_id = item["id"]
        preview = item["question"][:70].rstrip()

        input_data, expected_output, metadata = _build_dataset_item(item)

        if dry_run:
            log.info("  [dry-run] %s — %s", gq_id, preview)
            ok += 1
            continue

        try:
            assert client is not None  # noqa: S101 — guarded by dry_run check above
            client.create_dataset_item(
                dataset_name=dataset_name,
                input=input_data,
                expected_output=expected_output,
                metadata=metadata,
                # Stable upsert key — prevents duplicate items on re-upload
                # and keeps existing dataset-run links intact.
                id=gq_id,
            )
            log.info("  Uploaded  %s — %s", gq_id, preview)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            log.error(
                "  FAILED    %s — %s | %s: %s",
                gq_id,
                preview,
                type(exc).__name__,
                exc,
            )
            err += 1

    if client is not None:
        # Flush batched events before the process exits — without this, the
        # Langfuse SDK may drop the last items in its async buffer.
        client.flush()

    mode = "[dry-run] " if dry_run else ""
    suffix = f" — {err} error(s)" if err else ""
    log.info(
        "\n%sDone. %d/%d item(s) uploaded to dataset %r%s.",
        mode,
        ok,
        ok + err,
        dataset_name,
        suffix,
    )

    if err:
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload Q&A golden questions to a Langfuse dataset.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dataset-name",
        default=DEFAULT_DATASET_NAME,
        metavar="NAME",
        help=f"Langfuse dataset name (default: {DEFAULT_DATASET_NAME!r})",
    )
    parser.add_argument(
        "--json-path",
        type=Path,
        default=DEFAULT_JSON_PATH,
        metavar="PATH",
        help=(f"Path to the golden questions JSON file (default: {DEFAULT_JSON_PATH.relative_to(_REPO_ROOT)})"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview all items without making any Langfuse API calls.",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args()

    json_path: Path = args.json_path
    if not json_path.exists():
        log.error("JSON file not found: %s", json_path)
        sys.exit(1)

    log.info("Loading  %s …", json_path)
    with open(json_path, encoding="utf-8") as fh:
        raw = json.load(fh)

    if not isinstance(raw, list):
        log.error("Expected a JSON array; got %s", type(raw).__name__)
        sys.exit(1)

    questions: list[dict] = raw
    log.info("Loaded   %d question(s).", len(questions))

    # Full schema + uniqueness validation before touching the API.
    for idx, item in enumerate(questions):
        _validate_item(item, idx)
    _validate_no_duplicate_ids(questions)
    log.info("Validated %d item(s) — schema and uniqueness checks passed.", len(questions))

    if args.dry_run:
        log.info("[dry-run] No Langfuse API calls will be made.\n")

    upload(questions, dataset_name=args.dataset_name, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
