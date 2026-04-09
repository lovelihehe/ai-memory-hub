"""
Core module: data models, configuration, and utilities.

Provides foundational components used across all other modules.
"""

from ai_memory_hub.core.models import Evidence, MemoryRecord, RawEvent, utc_now
from ai_memory_hub.core.config import (
    BootstrapProject,
    LlmRefinementConfig,
    MemoryConfig,
    ObsidianConfig,
    ScanConfig,
    SourceConfig,
    ToolConfig,
    load_config,
)
from ai_memory_hub.core.utils import (
    MOJIBAKE_MARKERS,
    contains_mojibake,
    ensure_parent,
    load_records,
    load_records_with_mtime,
    normalize_project_reference,
    parse_timestamp,
    project_key_from_path,
    slugify,
    stable_id,
    time_ago,
    trim_excerpt,
)
from ai_memory_hub.core.logger import Logger, get_logger

__all__ = [
    # models
    "Evidence",
    "MemoryRecord",
    "RawEvent",
    "utc_now",
    # config
    "BootstrapProject",
    "LlmRefinementConfig",
    "MemoryConfig",
    "ObsidianConfig",
    "ScanConfig",
    "SourceConfig",
    "ToolConfig",
    "load_config",
    # utils
    "MOJIBAKE_MARKERS",
    "contains_mojibake",
    "ensure_parent",
    "load_records",
    "load_records_with_mtime",
    "normalize_project_reference",
    "parse_timestamp",
    "project_key_from_path",
    "slugify",
    "stable_id",
    "time_ago",
    "trim_excerpt",
    # logger
    "Logger",
    "get_logger",
]
