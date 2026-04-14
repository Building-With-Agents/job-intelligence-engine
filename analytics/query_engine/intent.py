"""Lightweight intent classification for analytics Q&A."""

from __future__ import annotations

from typing import Any


def classify_intent(question: str) -> dict[str, Any]:
    """Return a small structured intent object (deterministic baseline; LLM optional later)."""
    q = question.strip().lower()
    bucket = "general"
    if any(k in q for k in ("salary", "pay", "wage", "compensation")):
        bucket = "compensation"
    elif any(k in q for k in ("skill", "tool", "technology")):
        bucket = "skills_tools"
    elif any(k in q for k in ("role", "title", "canonical")):
        bucket = "roles"
    elif any(k in q for k in ("region", "geo", "borderplex", "location")):
        bucket = "geography"
    return {"bucket": bucket, "question_len": len(question)}
