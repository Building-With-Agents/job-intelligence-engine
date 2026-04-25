"""Regression: qa_eval --use-http must send LaborPulse headers (JIE #222)."""

from __future__ import annotations

import pytest

from eval.qa_eval_laborpulse_headers import laborpulse_analytics_query_headers


def test_laborpulse_headers_defaults_and_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANALYTICS_QUERY_X_TENANT_ID", raising=False)
    monkeypatch.delenv("ANALYTICS_QUERY_X_USER_EMAIL", raising=False)
    monkeypatch.delenv("ANALYTICS_QUERY_X_API_KEY", raising=False)
    h = laborpulse_analytics_query_headers(x_request_id="trace-1")
    assert h["Content-Type"] == "application/json"
    assert h["X-Tenant-Id"] == "borderplex"
    assert h["X-User-Email"] == "smoke@thewaifinder.com"
    assert h["X-Request-Id"] == "trace-1"
    assert "X-API-Key" not in h


def test_laborpulse_headers_overrides_and_api_key_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANALYTICS_QUERY_X_TENANT_ID", "  acme  ")
    monkeypatch.setenv("ANALYTICS_QUERY_X_USER_EMAIL", "eval@example.com")
    monkeypatch.setenv("ANALYTICS_QUERY_X_API_KEY", "secret-key")
    h = laborpulse_analytics_query_headers(x_request_id="r2")
    assert h["X-Tenant-Id"] == "acme"
    assert h["X-User-Email"] == "eval@example.com"
    assert h["X-Request-Id"] == "r2"
    assert h["X-API-Key"] == "secret-key"


def test_laborpulse_headers_tenant_falls_back_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANALYTICS_QUERY_X_TENANT_ID", "   ")
    monkeypatch.setenv("ANALYTICS_QUERY_X_USER_EMAIL", "   ")
    h = laborpulse_analytics_query_headers(x_request_id="r3")
    assert h["X-Tenant-Id"] == "borderplex"
    assert h["X-User-Email"] == "smoke@thewaifinder.com"
