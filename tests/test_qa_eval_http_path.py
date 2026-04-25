"""Integration tests for execute_qa_item --use-http path (JIE #255).

Verifies that execute_qa_item with use_http=True wires laborpulse_analytics_query_headers
into httpx.Client.post correctly so Content-Type, X-Tenant-Id, X-User-Email, and
X-Request-Id are always present. The original regression: all 90 v1-baseline items
returned http_400 because the header block was absent from the first implementation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from eval.qa_eval import execute_qa_item

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_RESPONSE_BODY: dict = {
    "answer": "Mocked answer.",
    "evidence": [{"title": "t", "snippet": "s"}],
    "confidence": "high",
    "confidence_flagged_low": False,
    "confidence_explanation": None,
    "volume_flagged_low": False,
    "volume_warning": None,
    "refused": False,
    "sql_execution_error_detail": None,
    "classified_intent": "geographic",
    "row_count_returned": 5,
}


def _http_mock(status: int = 200, body: dict | None = None) -> tuple:
    """Return (patch_ctx, mock_client_instance) for eval.qa_eval.httpx.Client."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body if body is not None else _GOOD_RESPONSE_BODY

    inst = MagicMock()
    inst.__enter__ = MagicMock(return_value=inst)
    inst.__exit__ = MagicMock(return_value=False)
    inst.post.return_value = resp
    return patch("eval.qa_eval.httpx.Client", return_value=inst), inst


# ---------------------------------------------------------------------------
# Header contract — the four mandatory headers
# ---------------------------------------------------------------------------


def test_use_http_sends_all_four_required_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Content-Type, X-Tenant-Id, X-User-Email, X-Request-Id are always present."""
    monkeypatch.setenv("ANALYTICS_QUERY_X_TENANT_ID", "borderplex")
    monkeypatch.setenv("ANALYTICS_QUERY_X_USER_EMAIL", "eval@jie.local")
    monkeypatch.delenv("ANALYTICS_QUERY_X_API_KEY", raising=False)

    patch_cm, mock_inst = _http_mock()
    with patch_cm:
        resp, err = execute_qa_item(
            question="Which roles are growing in El Paso?",
            correlation_id="gq-041-v1",
            use_http=True,
            analytics_base_url="http://127.0.0.1:8000",
        )

    assert err is None
    assert isinstance(resp, dict)
    _, kw = mock_inst.post.call_args
    h = kw["headers"]
    assert h["Content-Type"] == "application/json"
    assert h["X-Tenant-Id"] == "borderplex"
    assert h["X-User-Email"] == "eval@jie.local"
    assert h["X-Request-Id"] == "gq-041-v1"
    assert "X-API-Key" not in h


def test_use_http_includes_api_key_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-API-Key is forwarded when ANALYTICS_QUERY_X_API_KEY is non-empty."""
    monkeypatch.setenv("ANALYTICS_QUERY_X_API_KEY", "sk-abc123")

    patch_cm, mock_inst = _http_mock()
    with patch_cm:
        execute_qa_item(
            question="Q",
            correlation_id="gq-001-v1",
            use_http=True,
            analytics_base_url="http://127.0.0.1:8000",
        )

    _, kw = mock_inst.post.call_args
    assert kw["headers"]["X-API-Key"] == "sk-abc123"


def test_use_http_omits_api_key_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-API-Key is absent when ANALYTICS_QUERY_X_API_KEY is unset."""
    monkeypatch.delenv("ANALYTICS_QUERY_X_API_KEY", raising=False)

    patch_cm, mock_inst = _http_mock()
    with patch_cm:
        execute_qa_item(
            question="Q",
            correlation_id="gq-001-v1",
            use_http=True,
            analytics_base_url="http://127.0.0.1:8000",
        )

    _, kw = mock_inst.post.call_args
    assert "X-API-Key" not in kw["headers"]


def test_use_http_correlation_id_propagated_as_request_id() -> None:
    """correlation_id is used verbatim as X-Request-Id; not replaced with a UUID."""
    cid = "gq-055-v2-synthesis-tighten"

    patch_cm, mock_inst = _http_mock()
    with patch_cm:
        execute_qa_item(
            question="Q",
            correlation_id=cid,
            use_http=True,
            analytics_base_url="http://127.0.0.1:8000",
        )

    _, kw = mock_inst.post.call_args
    assert kw["headers"]["X-Request-Id"] == cid


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


def test_use_http_400_surfaces_as_pipeline_error() -> None:
    """HTTP 400 is returned as pipeline_error='http_400', not raised."""
    patch_cm, _ = _http_mock(status=400)
    with patch_cm:
        resp, err = execute_qa_item(
            question="Q",
            correlation_id="gq-001-v1",
            use_http=True,
            analytics_base_url="http://127.0.0.1:8000",
        )

    assert resp is None
    assert err == "http_400"


def test_use_http_502_surfaces_as_pipeline_error() -> None:
    """HTTP 5xx surfaces as pipeline_error so the item is scored, not skipped."""
    patch_cm, _ = _http_mock(status=502)
    with patch_cm:
        resp, err = execute_qa_item(
            question="Q",
            correlation_id="gq-001-v1",
            use_http=True,
            analytics_base_url="http://127.0.0.1:8000",
        )

    assert resp is None
    assert err == "http_502"


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


def test_use_http_trailing_slash_stripped_from_base_url() -> None:
    """Trailing slash on analytics_base_url is stripped before /analytics/query is appended."""
    patch_cm, mock_inst = _http_mock()
    with patch_cm:
        execute_qa_item(
            question="Q",
            correlation_id="x",
            use_http=True,
            analytics_base_url="http://127.0.0.1:8000/",
        )

    url = mock_inst.post.call_args[0][0]
    assert url == "http://127.0.0.1:8000/analytics/query"


def test_use_http_question_and_correlation_id_in_post_body() -> None:
    """POST body contains question and correlation_id for API traceability."""
    patch_cm, mock_inst = _http_mock()
    with patch_cm:
        execute_qa_item(
            question="What are the top skills in El Paso?",
            correlation_id="gq-042-v1",
            use_http=True,
            analytics_base_url="http://127.0.0.1:8000",
        )

    _, kw = mock_inst.post.call_args
    body = kw["json"]
    assert body["question"] == "What are the top skills in El Paso?"
    assert body["correlation_id"] == "gq-042-v1"
