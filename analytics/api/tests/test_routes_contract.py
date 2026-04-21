"""Contract tests for analytics API (mocked pipeline, no DB)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from analytics.api.app import create_app
from analytics.api.schemas import AnalyticsQueryResponse, EvidenceItem

from .laborpulse_headers import laborpulse_query_headers


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(
        "JIE_API_KEYS",
        json.dumps([{"key_id": "contract-test-key", "secret": "contract-test-secret"}]),
    )
    return TestClient(create_app())


def test_post_query_returns_schema(client: TestClient) -> None:
    body = AnalyticsQueryResponse(
        answer="ok",
        evidence=[EvidenceItem(title="t", source="s", snippet="n")],
        confidence=0.9,
        follow_up_questions=["q1"],
        sql_generated="SELECT 1",
        cost_usd=0.01,
    )
    with patch("analytics.api.routes.session_scope") as sc:
        sc.return_value.__enter__.return_value = MagicMock()
        with patch("analytics.api.routes.run_analytics_qna", return_value=body):
            r = client.post(
                "/analytics/query",
                json={"question": "hello"},
                headers=laborpulse_query_headers("contract-test-secret"),
            )
    assert r.status_code == 200
    data = r.json()
    required = (
        "conversation_id",
        "answer",
        "evidence",
        "confidence",
        "follow_up_questions",
        "cost_usd",
        "sql_generated",
    )
    for k in required:
        assert k in data
    assert data["answer"] == "ok"
    assert data["confidence"] == "high"
    assert isinstance(data["evidence"], list)
    assert len(data["evidence"]) == 1
    assert data["evidence"][0]["title"] == "t"
    uuid.UUID(data["conversation_id"])
    assert isinstance(data["follow_up_questions"], list)
    assert 2 <= len(data["follow_up_questions"]) <= 4
    assert data["sql_generated"] == "SELECT 1"
    assert isinstance(data["cost_usd"], (int, float))
    assert data["cost_usd"] == 0.01


def test_post_query_invalid_conversation_id_400(client: TestClient) -> None:
    r = client.post(
        "/analytics/query",
        json={"question": "hello", "conversation_id": "not-a-uuid"},
        headers=laborpulse_query_headers("contract-test-secret"),
    )
    assert r.status_code == 400
    assert r.json().get("detail") == "invalid_conversation_id"


def test_post_query_echoes_valid_conversation_id(client: TestClient) -> None:
    cid = str(uuid.uuid4())
    body = AnalyticsQueryResponse(
        answer="ok",
        evidence=[],
        confidence=0.7,
        follow_up_questions=["a", "b"],
        sql_generated="",
        cost_usd=0.0,
    )
    with patch("analytics.api.routes.session_scope") as sc:
        sc.return_value.__enter__.return_value = MagicMock()
        with patch("analytics.api.routes.run_analytics_qna", return_value=body):
            r = client.post(
                "/analytics/query",
                json={"question": "hello", "conversation_id": cid},
                headers=laborpulse_query_headers("contract-test-secret"),
            )
    assert r.status_code == 200
    assert r.json()["conversation_id"] == cid


def test_openapi_docs_available(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "/analytics/query" in spec["paths"]


def test_swagger_ui_docs_reachable(client: TestClient) -> None:
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()


def test_post_query_invalid_content_type_400(client: TestClient) -> None:
    h = laborpulse_query_headers("contract-test-secret")
    h["Content-Type"] = "text/plain"
    r = client.post(
        "/analytics/query",
        content=b'{"question":"hello"}',
        headers=h,
    )
    assert r.status_code == 400
    assert r.json().get("detail") == "invalid_content_type"


def test_post_query_invalid_json_400(client: TestClient) -> None:
    r = client.post(
        "/analytics/query",
        content=b"{not json",
        headers=laborpulse_query_headers("contract-test-secret"),
    )
    assert r.status_code == 400
    assert r.json().get("detail") == "invalid_json"


def test_post_query_missing_tenant_400(client: TestClient) -> None:
    h = laborpulse_query_headers("contract-test-secret")
    del h["X-Tenant-Id"]
    r = client.post("/analytics/query", json={"question": "hello"}, headers=h)
    assert r.status_code == 400
    assert r.json().get("detail") == "missing_tenant_id"


def test_post_query_missing_request_id_400(client: TestClient) -> None:
    h = laborpulse_query_headers("contract-test-secret")
    del h["X-Request-Id"]
    r = client.post("/analytics/query", json={"question": "hello"}, headers=h)
    assert r.status_code == 400
    assert r.json().get("detail") == "missing_request_id"


def test_post_query_empty_question_400(client: TestClient) -> None:
    r = client.post(
        "/analytics/query",
        json={"question": ""},
        headers=laborpulse_query_headers("contract-test-secret"),
    )
    assert r.status_code == 400
    assert r.json().get("detail") == "empty_question"


def test_post_query_too_broad_400(client: TestClient) -> None:
    r = client.post(
        "/analytics/query",
        json={"question": "Show me everything about postings"},
        headers=laborpulse_query_headers("contract-test-secret"),
    )
    assert r.status_code == 400
    assert r.json().get("detail") == "question_too_broad"


def test_post_query_pipeline_error_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "JIE_API_KEYS",
        json.dumps([{"key_id": "contract-test-key", "secret": "contract-test-secret"}]),
    )
    from analytics.api.app import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    with patch("analytics.api.routes.session_scope") as sc:
        sc.return_value.__enter__.return_value = MagicMock()
        with patch("analytics.api.routes.run_analytics_qna", side_effect=RuntimeError("boom")):
            r = client.post(
                "/analytics/query",
                json={"question": "hello"},
                headers=laborpulse_query_headers("contract-test-secret"),
            )
    assert r.status_code == 500
