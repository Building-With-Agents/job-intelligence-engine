"""Shared Pydantic types for the Job Intelligence Engine pipeline."""

from common.types.extraction_schemas import (
    ContextSignal,
    ResponsibilityRecord,
    TaskRecord,
)
from common.types.extraction_types import (
    ExtractionMetadata,
    SkillRecord,
    SpanRecord,
    TaxonomyResult,
    ToolRecord,
)
from common.types.job_record import JobRecord
from common.types.query_request import QueryPersona, QueryRequest
from common.types.raw_job_record import RawJobRecord
from common.types.region_config import RegionConfig

__all__ = [
    "ContextSignal",
    "ExtractionMetadata",
    "JobRecord",
    "QueryPersona",
    "QueryRequest",
    "RawJobRecord",
    "RegionConfig",
    "ResponsibilityRecord",
    "SkillRecord",
    "SpanRecord",
    "TaskRecord",
    "TaxonomyResult",
    "ToolRecord",
]
