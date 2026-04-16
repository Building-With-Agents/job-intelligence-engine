"""Contract tests for analytics API (mocked pipeline, no DB)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from analytics.api.app import create_app
from analytics.api.schemas import AnalyticsQueryResponse, EvidenceItem


@pytest.fixture
def client() -> TestClient:
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
            r = client.post("/analytics/query", json={"question": "hello"})
    assert r.status_code == 200
    data = r.json()
    assert data["answer"] == "ok"
    assert data["confidence"] == 0.9
    assert len(data["evidence"]) == 1


def test_openapi_docs_available(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "/analytics/query" in spec["paths"]


def test_swagger_ui_docs_reachable(client: TestClient) -> None:
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()
