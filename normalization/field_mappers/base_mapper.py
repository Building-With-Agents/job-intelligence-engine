"""Backward-compatibility shim — use ``normalization.mappers.base`` instead."""

from normalization.mappers.base import FieldMapper, MapperBase  # noqa: F401

__all__ = ["FieldMapper", "MapperBase"]
