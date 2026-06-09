"""Tests that demo smoke and preflight share one source of truth."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

real_query = importlib.import_module("scripts.smoke.laborpulse.real_query")
preflight = importlib.import_module("scripts.demo.preflight_check")
demo_common = importlib.import_module("scripts.smoke.laborpulse._demo_common")


def test_locked_demo_questions_match_between_scripts() -> None:
    assert real_query.LOCKED_DEMO_QUESTIONS == preflight.LOCKED_DEMO_QUESTIONS
    assert real_query.LOCKED_DEMO_QUESTIONS == demo_common.LOCKED_DEMO_QUESTIONS
    assert len(real_query.LOCKED_DEMO_QUESTIONS) == 5


def test_default_jie_base_url_honors_env(monkeypatch) -> None:
    monkeypatch.delenv("JIE_BASE_URL", raising=False)
    assert demo_common.default_jie_base_url() == "http://127.0.0.1:8000"

    monkeypatch.setenv("JIE_BASE_URL", "http://localhost:8010")
    assert demo_common.default_jie_base_url() == "http://localhost:8010"
