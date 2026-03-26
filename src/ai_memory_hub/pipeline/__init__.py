"""
流水线编排模块。

整合采集、提炼、索引全流程。
"""

from ai_memory_hub.pipeline.pipeline import (
    init_environment,
    run_collect,
    run_consolidate,
    run_index,
    run_pipeline,
)
from ai_memory_hub.pipeline.bootstrap import bootstrap_known_projects
from ai_memory_hub.pipeline.growth import memory_growth

__all__ = [
    "init_environment",
    "run_collect",
    "run_consolidate",
    "run_index",
    "run_pipeline",
    "bootstrap_known_projects",
    "memory_growth",
]
