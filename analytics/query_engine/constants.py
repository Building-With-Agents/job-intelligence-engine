"""Thresholds for analytics Q&A synthesis — aligned with Week 8 runbook.

Change only with product sign-off; keep in sync with `.cursor/rules/analytics-qna-synthesis.mdc`.
"""

from __future__ import annotations

# Flag transparency when blended confidence is below this (runbook: 0.6).
CONFIDENCE_TRANSPARENCY_THRESHOLD: float = 0.6

# Warn when the primary posting count supporting the answer is below this (runbook: 30).
VOLUME_WARNING_POSTING_THRESHOLD: int = 30

# JIE #330 — pre-query skill taxonomy gate: never blend classifier confidence above this
# when we refuse because labels are absent from dbo.skills or geo aggregates cannot
# scope by skill.
SKILL_TAXONOMY_GATE_CONFIDENCE_CAP: float = 0.35

# Generic refusal copy (no skill examples — keep in sync with product/legal review).
NO_DATA_SKILL_TAXONOMY_REFUSAL: str = (
    "No postings are tagged with the requested skill labels in the dataset taxonomy."
)

NO_DATA_GEO_SKILL_SCOPE_REFUSAL: str = (
    "Aggregate geographic demand here is not broken out by skill label; "
    "posting totals cannot be reported as skill-specific counts for this question shape."
)
