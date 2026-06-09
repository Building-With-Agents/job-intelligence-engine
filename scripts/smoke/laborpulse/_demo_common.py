"""Shared constants and helpers for LaborPulse demo smoke and preflight scripts."""

from __future__ import annotations

import os

LOCKED_DEMO_QUESTIONS: tuple[str, ...] = (
    "What are the top IT skills Borderplex employers are hiring for right now?",
    "What should a training program for AI agent developers look like given what Borderplex employers are hiring for right now?",
    "What should a training program for cybersecurity analysts look like given what Borderplex employers are hiring for right now?",
    "What tools and practices do Borderplex employers expect from workflow automation engineers?",
    "What does an MLOps role look like in the Borderplex job market right now?",
)

DEFAULT_JIE_BASE_URL = "http://127.0.0.1:8000"


def default_jie_base_url() -> str:
    """Return JIE API base URL; mirrors ``scripts/run_analytics_api.py`` default port."""
    return os.environ.get("JIE_BASE_URL", DEFAULT_JIE_BASE_URL)


def likely_refusal(answer: str) -> bool:
    a = (answer or "").strip().lower()
    if not a:
        return True
    prefixes = (
        "i cannot",
        "i can't",
        "unable to",
        "cannot answer",
        "no data",
        "not enough data",
    )
    return any(a.startswith(p) for p in prefixes)
