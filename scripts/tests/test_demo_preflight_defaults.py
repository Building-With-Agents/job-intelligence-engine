"""Unit tests for demo preflight JIE base URL defaults."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

preflight = importlib.import_module("scripts.demo.preflight_check")


def test_default_jie_base_url_matches_run_analytics_api(monkeypatch) -> None:
    monkeypatch.delenv("JIE_BASE_URL", raising=False)

    assert preflight._default_jie_base_url() == "http://127.0.0.1:8000"


def test_default_jie_base_url_honors_jie_base_url(monkeypatch) -> None:
    monkeypatch.setenv("JIE_BASE_URL", "http://localhost:8010")

    assert preflight._default_jie_base_url() == "http://localhost:8010"
