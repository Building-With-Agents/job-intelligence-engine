"""Normalization mappers — registry and factory.

Provides a registry of per-source field mappers and a factory function
to look them up by source name.
"""

from normalization.mappers.base import MapperBase
from normalization.mappers.crawl4ai_indeed import Crawl4AIIndeedMapper
from normalization.mappers.crawl4ai_usajobs import Crawl4AIUSAJobsMapper
from normalization.mappers.jsearch import JSearchMapper

MAPPER_REGISTRY: dict[str, MapperBase] = {
    "jsearch": JSearchMapper(),
    "crawl4ai": Crawl4AIIndeedMapper(),
    "crawl4ai_indeed": Crawl4AIIndeedMapper(),
    "crawl4ai_usajobs": Crawl4AIUSAJobsMapper(),
}


def get_mapper(source_name: str) -> MapperBase | None:
    """Return the mapper for a given source, or None if not registered."""
    return MAPPER_REGISTRY.get(source_name)


__all__ = [
    "Crawl4AIIndeedMapper",
    "Crawl4AIUSAJobsMapper",
    "JSearchMapper",
    "MAPPER_REGISTRY",
    "MapperBase",
    "get_mapper",
]
