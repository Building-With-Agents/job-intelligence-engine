"""Backward-compatibility shim — use ``normalization.mappers.crawl4ai_indeed`` instead."""

from normalization.mappers.crawl4ai_indeed import Crawl4AIIndeedMapper  # noqa: F401

# Keep the old name importable
ScraperMapper = Crawl4AIIndeedMapper

__all__ = ["Crawl4AIIndeedMapper", "ScraperMapper"]
