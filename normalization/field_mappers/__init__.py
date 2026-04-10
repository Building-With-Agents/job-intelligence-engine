"""Backward-compatibility shim — use ``normalization.mappers`` instead."""

from normalization.mappers.base import FieldMapper, MapperBase
from normalization.mappers.crawl4ai_indeed import Crawl4AIIndeedMapper
from normalization.mappers.jsearch import JSearchMapper

# Keep the old name importable
ScraperMapper = Crawl4AIIndeedMapper

__all__ = [
    "Crawl4AIIndeedMapper",
    "FieldMapper",
    "JSearchMapper",
    "MapperBase",
    "ScraperMapper",
]
