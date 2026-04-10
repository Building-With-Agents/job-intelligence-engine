from __future__ import annotations

"""Backward-compatible re-exports for skills extraction models.

Canonical definitions: ``common.types`` and ``extraction_schemas.py``.
"""

from common.types import ContextSignal, SpanRecord

__all__ = ["SpanRecord", "ContextSignal"]
