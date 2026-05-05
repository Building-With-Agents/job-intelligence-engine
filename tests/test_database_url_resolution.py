"""Unit tests for primary DB URL resolution (PYTHON_DATABASE_URL vs AZURE_POSTGRES_DATABASE_URL)."""

from __future__ import annotations

import pytest

from common.data_store.database import _resolve_primary_database_url


def test_prefers_python_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHON_DATABASE_URL", "postgresql+psycopg2://py/py")
    monkeypatch.setenv("AZURE_POSTGRES_DATABASE_URL", "postgresql+psycopg2://az/az")
    assert _resolve_primary_database_url() == "postgresql+psycopg2://py/py"


def test_falls_back_to_azure_postgres_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHON_DATABASE_URL", raising=False)
    monkeypatch.setenv("AZURE_POSTGRES_DATABASE_URL", "postgresql+psycopg2://az/az")
    assert _resolve_primary_database_url() == "postgresql+psycopg2://az/az"


def test_empty_when_neither_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHON_DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_POSTGRES_DATABASE_URL", raising=False)
    assert _resolve_primary_database_url() == ""
