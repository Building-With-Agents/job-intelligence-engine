"""Authoring form for Q&A golden questions (gq-XXX records).

Seeded with the 90 JIE-aligned questions from
`lesson-framework/week-09/readings/reading-jsis-query-reference.md`
in the curriculum-planning repo. Devs use this form to fill in the
`context`, `ideal_answer_summary`, `must_include`, `must_not_include`,
and `difficulty` fields for each question, then copy the finished JSON
into `eval/qa_golden_questions.json` for Langfuse upload.

The `golden_questions.json` file in this directory IS the persistent
store — every edit writes back to it atomically. There is no separate
seed vs. output file.

Run (from repo root):
    streamlit run tools/golden-questions-form/app.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import streamlit as st
from pydantic import BaseModel, Field, ValidationError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_THIS_DIR = Path(__file__).resolve().parent
_OUTPUT_FILE = _THIS_DIR / "golden_questions.json"
_TMP_FILE = _THIS_DIR / "golden_questions.json.tmp"

# Built-in intent categories from the Q&A data contract. New intents can still
# be added from the UI — the form stores whatever value the user picks.
_BUILTIN_INTENTS = [
    "disruption",
    "emergence",
    "trend",
    "role_evolution",
    "geographic",
    "comparison",
    "employer",
    "curriculum",
    "workflow",
]

# Built-in difficulty values. Contract uses "standard"/"hard"; "easy"/"medium"
# included for authoring flexibility. Like intents, users can add new values.
_BUILTIN_DIFFICULTIES = ["easy", "medium", "hard", "standard"]

# Canonical field order for JSON serialization (matches the Week 9 runbook).
_FIELD_ORDER = [
    "id",
    "question",
    "source",
    "intent",
    "context",
    "ideal_answer_summary",
    "must_include",
    "must_not_include",
    "difficulty",
]

_NEW_SENTINEL = "+ Add new…"
_NEW_RECORD_SENTINEL = "+ New question"


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class GoldenQuestion(BaseModel):
    """A single golden-question record.

    `intent` and `difficulty` are free strings rather than Literals so the
    form can introduce new categories without a schema change. Validation
    is limited to non-empty required fields — category vocabulary is not
    enforced here, only rendered for discoverability in the UI.
    """

    id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    source: str = "wfd_archetype"
    intent: str = Field(..., min_length=1)
    context: str = ""
    ideal_answer_summary: str = ""
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    difficulty: str = "standard"

    def to_ordered_dict(self) -> dict:
        """Return the record as a dict in canonical field order."""
        raw = self.model_dump()
        return {k: raw[k] for k in _FIELD_ORDER if k in raw}


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _load_raw_records() -> tuple[list[dict], list[str]]:
    """Return (records, warnings). A warning is emitted for each row that
    fails Pydantic validation so the UI can show them without crashing."""
    if not _OUTPUT_FILE.exists():
        return [], []
    try:
        text = _OUTPUT_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"Cannot read {_OUTPUT_FILE}: {exc}") from exc
    if not text:
        return [], []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_OUTPUT_FILE} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError(
            f"{_OUTPUT_FILE} is not a JSON array "
            f"(got {type(data).__name__})."
        )

    records: list[dict] = []
    warnings: list[str] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            warnings.append(f"Row {i} is not an object, skipped.")
            continue
        try:
            GoldenQuestion.model_validate(row)
            records.append(row)
        except ValidationError as exc:
            warnings.append(f"Row {i} (id={row.get('id', '?')}) failed validation: {exc}")
            records.append(row)  # keep it so it can be fixed via the form
    return records, warnings


def _atomic_write(records: list[dict]) -> None:
    """Serialize and write atomically."""
    serialized = json.dumps(records, indent=2, ensure_ascii=False)
    _TMP_FILE.write_text(serialized + "\n", encoding="utf-8")
    os.replace(_TMP_FILE, _OUTPUT_FILE)


def _split_tokens(blob: str) -> list[str]:
    return [line.strip() for line in blob.splitlines() if line.strip()]


def _known_intents(records: list[dict]) -> list[str]:
    seen = {r.get("intent") for r in records if isinstance(r, dict) and r.get("intent")}
    return sorted(set(_BUILTIN_INTENTS) | seen)


def _known_difficulties(records: list[dict]) -> list[str]:
    seen = {r.get("difficulty") for r in records if isinstance(r, dict) and r.get("difficulty")}
    return sorted(set(_BUILTIN_DIFFICULTIES) | seen)


def _next_suggested_id(records: list[dict]) -> str:
    """Suggest gq-NNN for the next record based on max existing numeric id."""
    max_n = 0
    for r in records:
        rid = r.get("id", "") if isinstance(r, dict) else ""
        if rid.startswith("gq-"):
            try:
                max_n = max(max_n, int(rid[3:]))
            except ValueError:
                continue
    return f"gq-{max_n + 1:03d}"


def _last_modified() -> str:
    if not _OUTPUT_FILE.exists():
        return "(no file yet)"
    ts = datetime.fromtimestamp(_OUTPUT_FILE.stat().st_mtime)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------


st.set_page_config(
    page_title="Golden Questions Authoring",
    page_icon="📝",
    layout="wide",
)
st.title("📝 Golden Questions — authoring form")
st.caption(
    "Fill in the scoring criteria for each question, then copy the JSON file "
    "into `eval/qa_golden_questions.json` when your intents are complete."
)

# --- Load state ------------------------------------------------------------

try:
    records, warnings = _load_raw_records()
    load_error: str | None = None
except RuntimeError as exc:
    records, warnings = [], []
    load_error = str(exc)

intents_list = _known_intents(records)
difficulties_list = _known_difficulties(records)
existing_ids = {r.get("id") for r in records if isinstance(r, dict)}

# --- Sidebar: file status + record selection -------------------------------

with st.sidebar:
    st.header("File status")
    st.metric("Records on disk", len(records))
    st.write(f"**Last modified:** {_last_modified()}")
    st.write("**Output file:**")
    st.code(str(_OUTPUT_FILE), language="text")
    if load_error:
        st.error(load_error)
    for w in warnings:
        st.warning(w)

    st.divider()
    st.header("Select record")

    intent_filter = st.selectbox(
        "Filter by intent",
        options=["All"] + intents_list,
        help="Choose an intent category to narrow the record list below.",
    )

    if intent_filter == "All":
        filtered = records
    else:
        filtered = [r for r in records if isinstance(r, dict) and r.get("intent") == intent_filter]

    st.caption(f"{len(filtered)} record(s) match the filter.")

    # Record picker: existing records + "+ New question" sentinel
    picker_options = [_NEW_RECORD_SENTINEL] + [
        f"{r.get('id', '(no id)')} — {(r.get('question') or '')[:60]}"
        for r in filtered
    ]
    picked = st.selectbox("Record", options=picker_options)

# Determine whether we're in "edit" or "new" mode, and pre-populate the form.
if picked == _NEW_RECORD_SENTINEL:
    mode = "new"
    current = {
        "id": _next_suggested_id(records),
        "question": "",
        "source": "wfd_archetype",
        "intent": (intent_filter if intent_filter != "All" else _BUILTIN_INTENTS[0]),
        "context": "",
        "ideal_answer_summary": "",
        "must_include": [],
        "must_not_include": [],
        "difficulty": "standard",
    }
else:
    mode = "edit"
    # Parse id from the "id — question" picker label
    picked_id = picked.split(" — ", 1)[0]
    current = next(
        (r for r in filtered if isinstance(r, dict) and r.get("id") == picked_id),
        None,
    ) or filtered[0]  # fallback; shouldn't happen


# --- Main form -------------------------------------------------------------

st.subheader(f"{'Edit' if mode == 'edit' else 'Add new'} record")

with st.form("golden_question_form", clear_on_submit=False):
    col1, col2 = st.columns([2, 1])
    with col1:
        rec_id = st.text_input(
            "id",
            value=current.get("id", ""),
            help="Unique identifier (gq-NNN). Must not duplicate an existing record.",
            disabled=(mode == "edit"),  # editing an id is disallowed to keep lookups stable
        )
    with col2:
        # Difficulty widget with "+ Add new…" affordance
        diff_choices = difficulties_list + [_NEW_SENTINEL]
        current_diff = current.get("difficulty", "standard")
        if current_diff not in diff_choices:
            diff_choices = [current_diff] + diff_choices
        diff_selection = st.selectbox(
            "difficulty",
            options=diff_choices,
            index=diff_choices.index(current_diff) if current_diff in diff_choices else 0,
        )
        if diff_selection == _NEW_SENTINEL:
            difficulty = st.text_input(
                "new difficulty label",
                placeholder="e.g., refusal_test",
            ).strip()
        else:
            difficulty = diff_selection

    question = st.text_area(
        "question",
        value=current.get("question", ""),
        height=80,
    )

    col3, col4 = st.columns([1, 2])
    with col3:
        # Intent widget with "+ Add new…" affordance
        intent_choices = intents_list + [_NEW_SENTINEL]
        current_intent = current.get("intent", _BUILTIN_INTENTS[0])
        if current_intent not in intent_choices:
            intent_choices = [current_intent] + intent_choices
        intent_selection = st.selectbox(
            "intent",
            options=intent_choices,
            index=intent_choices.index(current_intent) if current_intent in intent_choices else 0,
        )
        if intent_selection == _NEW_SENTINEL:
            intent = st.text_input(
                "new intent label",
                placeholder="e.g., workforce_policy",
            ).strip()
        else:
            intent = intent_selection
    with col4:
        context = st.text_input(
            "context",
            value=current.get("context", ""),
            help="Decision context. What is the wfd_archetype trying to decide?",
        )

    source = st.text_input(
        "source",
        value=current.get("source", "wfd_archetype"),
        help="Usually 'wfd_archetype'. Override only if the question models a different persona.",
    )

    ideal_answer_summary = st.text_area(
        "ideal_answer_summary",
        value=current.get("ideal_answer_summary", ""),
        height=160,
        help="What a correct answer must include — specific data sources, ranges, timeframes, actions.",
    )

    col5, col6 = st.columns(2)
    with col5:
        must_include_blob = st.text_area(
            "must_include (one token per line)",
            value="\n".join(current.get("must_include", [])),
            height=140,
            help="Required elements the eval harness checks for.",
        )
    with col6:
        must_not_include_blob = st.text_area(
            "must_not_include (one token per line)",
            value="\n".join(current.get("must_not_include", [])),
            height=140,
            help="Anti-patterns that disqualify the answer.",
        )

    # Compose preview record in canonical order
    preview = {
        "id": rec_id.strip(),
        "question": question.strip(),
        "source": source.strip() or "wfd_archetype",
        "intent": intent,
        "context": context.strip(),
        "ideal_answer_summary": ideal_answer_summary.strip(),
        "must_include": _split_tokens(must_include_blob),
        "must_not_include": _split_tokens(must_not_include_blob),
        "difficulty": difficulty,
    }
    preview = {k: preview[k] for k in _FIELD_ORDER}

    with st.expander("Preview JSON (before save)"):
        st.code(
            json.dumps(preview, indent=2, ensure_ascii=False),
            language="json",
        )

    submit_label = "Save edits" if mode == "edit" else "Add question"
    submitted = st.form_submit_button(submit_label, type="primary")

# --- Save handling ---------------------------------------------------------

if submitted:
    errors: list[str] = []

    # Pydantic validation
    try:
        GoldenQuestion.model_validate(preview)
    except ValidationError as exc:
        errors.append(f"Schema validation failed: {exc}")

    if mode == "new" and preview["id"] in existing_ids:
        errors.append(
            f"id {preview['id']!r} already exists in the file. "
            "Pick a different id."
        )

    if errors:
        for msg in errors:
            st.error(msg)
        st.warning("File was not modified.")
    else:
        try:
            # Re-read right before writing to reduce race with a parallel session.
            current_records, _ = _load_raw_records()
            if mode == "new":
                if any(r.get("id") == preview["id"] for r in current_records if isinstance(r, dict)):
                    st.error(
                        f"id {preview['id']!r} appeared in the file since page load. "
                        "Refresh and retry."
                    )
                else:
                    current_records.append(preview)
                    _atomic_write(current_records)
                    st.success(
                        f"Added {preview['id']!r}. "
                        f"File now contains {len(current_records)} record(s)."
                    )
            else:  # edit
                replaced = False
                for i, r in enumerate(current_records):
                    if isinstance(r, dict) and r.get("id") == preview["id"]:
                        current_records[i] = preview
                        replaced = True
                        break
                if not replaced:
                    st.error(
                        f"id {preview['id']!r} no longer exists in the file. "
                        "Refresh the page."
                    )
                else:
                    _atomic_write(current_records)
                    st.success(
                        f"Saved edits to {preview['id']!r}. "
                        f"File contains {len(current_records)} record(s)."
                    )
                    records = current_records

            st.code(
                json.dumps(preview, indent=2, ensure_ascii=False),
                language="json",
            )
        except (OSError, RuntimeError) as exc:
            st.error(f"Write failed: {exc}")

# --- Recent submissions expander ------------------------------------------

with st.expander(f"Recent submissions (last {min(3, len(records))} of {len(records)})"):
    if not records:
        st.info("No records yet.")
    else:
        for rec in records[-3:][::-1]:
            st.code(
                json.dumps(rec, indent=2, ensure_ascii=False),
                language="json",
            )
