"""Backward-compatibility shim — use ``normalization.mappers.jsearch`` instead."""

from normalization.mappers.jsearch import JSearchMapper  # noqa: F401

__all__ = ["JSearchMapper"]
