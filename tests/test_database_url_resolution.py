"""Unit tests for primary DB URL resolution (PYTHON_DATABASE_URL vs AZURE_POSTGRES_DATABASE_URL)."""

from __future__ import annotations

import pytest

from common.data_store.database import _resolve_primary_database_url


def test_prefers_python_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHON_DATABASE_URL", "postgresql+psycopg2://py/py")
    monkeypatch.setenv("AZURE_POSTGRES_DATABASE_URL", "postgresql+psycopg2://az/az")
    monkeypatch.setenv("JIE_ALLOW_AZURE_DB_FALLBACK", "1")
    # Python URL still wins even when fallback is allowed.
    assert _resolve_primary_database_url() == "postgresql+psycopg2://py/py"


def test_no_azure_fallback_without_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHON_DATABASE_URL", raising=False)
    monkeypatch.setenv("AZURE_POSTGRES_DATABASE_URL", "postgresql+psycopg2://az/az")
    monkeypatch.delenv("JIE_ALLOW_AZURE_DB_FALLBACK", raising=False)
    assert _resolve_primary_database_url() == ""


def test_azure_fallback_with_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHON_DATABASE_URL", raising=False)
    monkeypatch.setenv("AZURE_POSTGRES_DATABASE_URL", "postgresql+psycopg2://az/az")
    monkeypatch.setenv("JIE_ALLOW_AZURE_DB_FALLBACK", "1")
    assert _resolve_primary_database_url() == "postgresql+psycopg2://az/az"


def test_empty_when_neither_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHON_DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_POSTGRES_DATABASE_URL", raising=False)
    monkeypatch.setenv("JIE_ALLOW_AZURE_DB_FALLBACK", "1")
    assert _resolve_primary_database_url() == ""
