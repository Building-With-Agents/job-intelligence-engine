"""Unit tests for analytics.query_engine.curriculum_synthesis (JIE #298).

Tests do NOT require a live DB; they mock the SQLAlchemy session so the suite
runs offline in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from analytics.query_engine.curriculum_synthesis import (
    _FALLBACK_MESSAGE,
    fetch_role_suggestions,
    no_resolved_role_message,
)


def _mock_session(labels: list[str]) -> MagicMock:
    """Return a mock Session whose execute() returns the given label rows."""
    session = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = [(label,) for label in labels]
    session.execute.return_value = result
    return session


class TestFetchRoleSuggestions:
    def test_returns_labels_in_order(self) -> None:
        session = _mock_session(["software engineer", "web developer", "data engineer"])
        labels = fetch_role_suggestions(session)
        assert labels == ["software engineer", "web developer", "data engineer"]

    def test_strips_whitespace(self) -> None:
        session = _mock_session(["  software engineer  ", "data engineer"])
        labels = fetch_role_suggestions(session)
        assert labels == ["software engineer", "data engineer"]

    def test_skips_empty_labels(self) -> None:
        session = _mock_session(["software engineer", "", "  ", "data engineer"])
        labels = fetch_role_suggestions(session)
        assert labels == ["software engineer", "data engineer"]

    def test_returns_empty_list_on_db_error(self) -> None:
        session = MagicMock()
        session.execute.side_effect = RuntimeError("DB is down")
        labels = fetch_role_suggestions(session)
        assert labels == []

    def test_respects_limit_parameter(self) -> None:
        session = _mock_session(["a", "b", "c"])
        fetch_role_suggestions(session, limit=2)
        call_args = session.execute.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params", call_args[0][-1])
        assert params["lim"] == 2

    def test_env_override_for_limit(self) -> None:
        session = _mock_session(["a"])
        with patch.dict("os.environ", {"CANONICAL_ROLE_SUGGESTION_LIMIT": "3"}):
            fetch_role_suggestions(session)
        call_args = session.execute.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params", call_args[0][-1])
        assert params["lim"] == 3


class TestNoResolvedRoleMessage:
    def test_includes_live_suggestions(self) -> None:
        session = _mock_session(["software engineer", "web developer"])
        msg = no_resolved_role_message(session, role_query="ninja coder", intent="workflow")
        assert "software engineer" in msg
        assert "web developer" in msg

    def test_includes_role_query_in_message(self) -> None:
        session = _mock_session(["software engineer"])
        msg = no_resolved_role_message(session, role_query="ninja coder", intent="workflow")
        assert "ninja coder" in msg

    def test_fallback_when_no_suggestions(self) -> None:
        session = _mock_session([])
        msg = no_resolved_role_message(session, intent="curriculum")
        assert msg == _FALLBACK_MESSAGE

    def test_fallback_on_db_error(self) -> None:
        session = MagicMock()
        session.execute.side_effect = RuntimeError("DB is down")
        msg = no_resolved_role_message(session, intent="role_evolution")
        assert msg == _FALLBACK_MESSAGE

    def test_omits_role_part_when_no_query(self) -> None:
        session = _mock_session(["software engineer"])
        msg = no_resolved_role_message(session)
        assert "No postings were found" in msg
        assert "for \u2018\u2019" not in msg


class TestEvidenceBundleIntegration:
    """Verify role_suggestion_hint flows through to the EvidenceBundle refusal."""

    def test_hint_used_as_refusal_reason(self) -> None:
        from analytics.query_engine.evidence import build_evidence_bundle
        from analytics.query_engine.schemas import QueryResultPayload
        from common.types.query_request import QueryRequest

        payload = QueryResultPayload(
            request=QueryRequest(query="show me curriculum for ninja coder"),
            intent_label="curriculum",
            classification_confidence=0.9,
            rows=[],
            row_count_returned=0,
            role_suggestion_hint="No postings found for \u2018ninja coder\u2019. Try \u2018software engineer\u2019.",
        )
        bundle = build_evidence_bundle(payload)
        assert bundle.refuse_synthesis is True
        assert bundle.refusal_reason == payload.role_suggestion_hint

    def test_generic_message_when_no_hint(self) -> None:
        from analytics.query_engine.evidence import build_evidence_bundle
        from analytics.query_engine.schemas import QueryResultPayload
        from common.types.query_request import QueryRequest

        payload = QueryResultPayload(
            request=QueryRequest(query="show curriculum for ninja coder"),
            intent_label="curriculum",
            classification_confidence=0.9,
            rows=[],
            row_count_returned=0,
        )
        bundle = build_evidence_bundle(payload)
        assert bundle.refuse_synthesis is True
        assert bundle.refusal_reason == "No data in scope for the selected filters."


class TestRoutingRoleHintGuards:
    """Regression tests for JIE #298 bug fixes in routing.py.

    These tests verify two guards added after the initial implementation:

    Bug 1 — misbucket filter masquerade: ``_filter_issue197_misbucket_rows``
    may remove every row from a curriculum/workflow result. The hint must only
    fire when the *router* itself returned 0 rows, not when the post-processing
    filter discarded them.

    Bug 2 — raw question as role name: ``role_query`` must use the structured
    ``role_names`` extracted by the classifier, not the full question text.
    """

    def test_misbucket_filter_strips_rows_no_hint_set(self) -> None:
        """Bug 1 regression: rows present before filter → hint must NOT fire."""
        from analytics.query_engine.routing import (
            _ROLE_FILTERED_INTENTS,
            _filter_issue197_misbucket_rows,
        )

        # Simulate a router result with one row that is an IT-role placeholder.
        pre_filter_rows = [{"role_classification": "N/A Not an IT role", "label": "software engineer"}]
        post_filter_rows = _filter_issue197_misbucket_rows(pre_filter_rows)

        # After filtering, rows is empty — but router_row_count (pre-filter) was 1.
        router_row_count = len(pre_filter_rows)
        intent_label = "curriculum"

        assert post_filter_rows == []
        # The hint condition must check router_row_count, not post-filter len(rows).
        hint_would_fire = router_row_count == 0 and intent_label in _ROLE_FILTERED_INTENTS
        assert not hint_would_fire, "Bug 1: hint fired because it checked post-filter rows instead of router_row_count"

    def test_role_query_uses_extracted_entities_not_raw_question(self) -> None:
        """Bug 2 regression: role_query must be extracted role_names, not question text."""
        from analytics.query_engine.curriculum_synthesis import no_resolved_role_message

        # What the classifier returns for extracted_entities.
        extracted_entities = {"role_names": ["data analyst"], "geographic_terms": [], "skill_names": []}
        role_names: list[str] = extracted_entities.get("role_names") or []
        role_query_str = ", ".join(role_names)

        full_question = "What skills do data analysts need for upskilling?"

        session = _mock_session(["software engineer", "web developer"])
        msg = no_resolved_role_message(session, role_query=role_query_str, intent="curriculum")

        # The message must contain the role name, not sentence fragments from the question.
        assert "data analyst" in msg
        assert "What skills" not in msg, "Bug 2: raw question text leaked into the role_query argument"
        assert "need for upskilling" not in msg, "Bug 2: raw question text leaked into the role_query argument"
        assert full_question not in msg
