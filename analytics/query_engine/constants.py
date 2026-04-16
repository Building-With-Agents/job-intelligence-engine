"""Thresholds for analytics Q&A synthesis — aligned with Week 8 runbook.

Change only with product sign-off; keep in sync with `.cursor/rules/analytics-qna-synthesis.mdc`.
"""

from __future__ import annotations

# Flag transparency when blended confidence is below this (runbook: 0.6).
CONFIDENCE_TRANSPARENCY_THRESHOLD: float = 0.6

# Warn when the primary posting count supporting the answer is below this (runbook: 30).
VOLUME_WARNING_POSTING_THRESHOLD: int = 30
