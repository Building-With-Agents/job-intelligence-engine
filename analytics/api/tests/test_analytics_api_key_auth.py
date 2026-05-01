"""JIE #226 — X-API-Key allowlist for POST /analytics/query."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from analytics.api.schemas import AnalyticsQueryResponse, EvidenceItem

from .laborpulse_headers import laborpulse_query_headers


@pytest.fixture
def api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "JIE_API_KEYS",
        json.dumps(
            [
                {"key_id": "wfdos-dev-2026q2", "secret": "supersecret"},
                {"key_id": "backup-key", "secret": "othersecret"},
            ]
        ),
    )


def test_load_api_keys_invalid_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from analytics.api.analytics_api_keys import load_api_keys

    monkeypatch.setenv("JIE_API_KEYS", "[{key_id:x,secret:y}]")
    monkeypatch.delenv("JIE_API_KEYS_FILE", raising=False)
    with pytest.raises(ValueError, match="jie_api_keys_invalid_json"):
        load_api_keys()


def test_missing_x_api_key_401(api_key_env: None) -> None:
    from analytics.api.app import create_app

    client = TestClient(create_app())
    with patch("analytics.api.routes.session_scope") as sc:
        sc.return_value.__enter__.return_value = MagicMock()
        with patch("analytics.api.routes.run_analytics_qna") as runq:
            runq.return_value = AnalyticsQueryResponse(
                answer="ok",
                evidence=[],
                confidence=0.5,
                follow_up_questions=[],
                sql_generated="",
                cost_usd=0.0,
            )
            r = client.post(
                "/analytics/query",
                json={"question": "hello"},
                headers=laborpulse_query_headers(None),
            )
    assert r.status_code == 401
    assert r.json().get("detail") == "missing_api_key"


def test_invalid_x_api_key_401(api_key_env: None) -> None:
    from analytics.api.app import create_app

    client = TestClient(create_app())
    r = client.post(
        "/analytics/query",
        json={"question": "hello"},
        headers=laborpulse_query_headers("not-in-list"),
    )
    assert r.status_code == 401
    assert r.json().get("detail") == "invalid_api_key"


def test_valid_x_api_key_200(api_key_env: None) -> None:
    from analytics.api.app import create_app

    client = TestClient(create_app())
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
                headers=laborpulse_query_headers("othersecret"),
            )
    assert r.status_code == 200
    data = r.json()
    assert data["answer"] == "ok"
    assert data["confidence"] == "high"
    assert 2 <= len(data["follow_up_questions"]) <= 4
    assert "conversation_id" in data


def test_logs_key_id_not_secret(api_key_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from analytics.api import routes

    events: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _capture(*args: object, **kwargs: object) -> None:
        events.append((args, kwargs))

    monkeypatch.setattr(routes.log, "info", _capture)

    from analytics.api.app import create_app

    client = TestClient(create_app())
    with patch("analytics.api.routes.session_scope") as sc:
        sc.return_value.__enter__.return_value = MagicMock()
        with patch("analytics.api.routes.run_analytics_qna") as runq:
            runq.return_value = AnalyticsQueryResponse(
                answer="ok",
                evidence=[],
                confidence=0.9,
                follow_up_questions=["q1"],
                sql_generated="SELECT 1",
                cost_usd=0.0,
            )
            r = client.post(
                "/analytics/query",
                json={"question": "hello"},
                headers=laborpulse_query_headers("supersecret"),
            )
    assert r.status_code == 200
    blob = json.dumps(events, default=str)
    assert "wfdos-dev-2026q2" in blob
    assert "key_id" in blob
    assert "supersecret" not in blob


def test_no_keys_misconfigured_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIE_API_KEYS", raising=False)
    monkeypatch.delenv("JIE_API_KEYS_FILE", raising=False)
    monkeypatch.delenv("LABORPULSE_ALLOW_NO_API_KEYS", raising=False)

    from analytics.api.app import create_app

    client = TestClient(create_app())
    r = client.post(
        "/analytics/query",
        json={"question": "hello"},
        headers=laborpulse_query_headers("irrelevant"),
    )
    assert r.status_code == 500
    assert r.json().get("detail") == "server_misconfigured_no_keys"


def test_allow_no_api_keys_dev_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIE_API_KEYS", raising=False)
    monkeypatch.delenv("JIE_API_KEYS_FILE", raising=False)
    monkeypatch.setenv("LABORPULSE_ALLOW_NO_API_KEYS", "1")

    from analytics.api.app import create_app

    client = TestClient(create_app())
    body = AnalyticsQueryResponse(
        answer="ok",
        evidence=[],
        confidence=0.75,
        follow_up_questions=[],
        sql_generated="",
        cost_usd=0.0,
    )
    with patch("analytics.api.routes.session_scope") as sc:
        sc.return_value.__enter__.return_value = MagicMock()
        with patch("analytics.api.routes.run_analytics_qna", return_value=body):
            r = client.post(
                "/analytics/query",
                json={"question": "hello"},
                headers=laborpulse_query_headers(None),
            )
    assert r.status_code == 200
    lp = r.json()
    assert lp["confidence"] == "medium"
    assert 2 <= len(lp["follow_up_questions"]) <= 4
