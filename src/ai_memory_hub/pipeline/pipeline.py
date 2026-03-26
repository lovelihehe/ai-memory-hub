"""
流水线编排模块。

提供四个顶层入口函数：
- init_environment: 初始化环境
- run_collect: 采集原始事件
- run_consolidate: 提炼候选记忆
- run_index: 重建索引并渲染
- run_pipeline: 运行完整流水线
"""

from __future__ import annotations

from ai_memory_hub.core.config import load_config
from ai_memory_hub.extraction.quality import govern_candidates, repair_data
from ai_memory_hub.extraction.extractors import consolidate
from ai_memory_hub.services.obsidian import ensure_vault_layout, sync_obsidian_vault
from ai_memory_hub.pipeline.bootstrap import bootstrap_known_projects
from ai_memory_hub.services.render import render_outputs
from ai_memory_hub.extraction.sources import collect_sources
from ai_memory_hub.storage.db import MemoryStore


def init_environment() -> dict[str, str]:
    config = load_config()
    store = MemoryStore(config)
    store.ensure_layout()
    vault_root = ensure_vault_layout(config)
    return {
        "app_home": str(config.app_home_path),
        "data_home": str(config.data_home_path),
        "db_path": str(store.db_path),
        "obsidian_vault": str(vault_root),
    }


def run_collect() -> dict[str, int]:
    config = load_config()
    store = MemoryStore(config)
    return collect_sources(config, store)


def run_consolidate() -> dict[str, int]:
    config = load_config()
    store = MemoryStore(config)
    return consolidate(config, store)


def run_index(incremental: bool = True) -> dict[str, int | str]:
    config = load_config()
    store = MemoryStore(config)
    indexed = store.rebuild_memory_index(incremental=incremental)
    rendered = render_outputs(config, store)
    obsidian = sync_obsidian_vault(config, store)
    return {
        "indexed_memories": indexed,
        "active_memories": store.count_memories(status="active"),
        "candidate_memories": store.count_memories(status="candidate"),
        **rendered,
        **obsidian,
    }


def run_pipeline() -> dict[str, int | str]:
    config = load_config()
    store = MemoryStore(config)
    store.ensure_layout()

    expired_count = store.cleanup_expired_memories()
    collect_stats = collect_sources(config, store)
    repair_stats = repair_data(store)
    consolidate_stats = consolidate(config, store)
    bootstrap_stats = bootstrap_known_projects(store)
    governance_stats = govern_candidates(store)
    index_stats = run_index(incremental=False)
    return {
        **collect_stats,
        **repair_stats,
        **consolidate_stats,
        **bootstrap_stats,
        **governance_stats,
        **index_stats,
        "expired_cleaned": expired_count,
    }
