from __future__ import annotations

from ai_memory_hub.core.config import load_config
from ai_memory_hub.extraction.quality import collect_memory_quality_signals
from ai_memory_hub.integrations.mcp_server import mcp_runtime_status
from ai_memory_hub.services.search import memory_context, memory_search
from ai_memory_hub.storage.db import MemoryStore


def run_doctor() -> dict:
    config = load_config()
    store = MemoryStore(config)
    checks: list[dict] = []

    config_exists = config.config_path.exists()
    checks.append(
        {
            "name": "config",
            "status": "passed" if config_exists else "failed",
            "details": [f"config_path={config.config_path}"],
        }
    )

    try:
        store.ensure_layout()
        data_home_accessible = config.data_home_path.exists()
        with store.connect() as conn:
            conn.execute("select 1").fetchone()
        checks.append(
            {
                "name": "storage",
                "status": "passed" if data_home_accessible else "failed",
                "details": [f"data_home={config.data_home_path}", f"db_path={store.db_path}"],
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "storage",
                "status": "failed",
                "details": [str(exc)],
            }
        )

    mcp_status = mcp_runtime_status()
    checks.append(
        {
            "name": "mcp_dependency",
            "status": "passed" if mcp_status["available"] else "warning",
            "details": [mcp_status["dependency"]] if mcp_status["available"] else [mcp_status["reason"], mcp_status["install_command"]],
        }
    )

    try:
        search_results = memory_search(store, query="local-first, python-only", tool="codex", limit=5)
        context_results = memory_context(
            store,
            tool="codex",
            repo=str(config.app_home_path),
            task_type="implementation",
            query="release-check, local-first",
        )
        checks.append(
            {
                "name": "query_path",
                "status": "passed",
                "details": [
                    f"search_results={len(search_results)}",
                    f"context_must_follow={len(context_results['must_follow'])}",
                    f"context_known_patterns={len(context_results['known_patterns'])}",
                ],
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "query_path",
                "status": "failed",
                "details": [str(exc)],
            }
        )

    quality = collect_memory_quality_signals(store)
    candidate_health = quality["candidate_health"]
    has_quality_issues = any(
        [
            quality["invalid_stability_memories"],
            quality["invalid_memory_timestamps"],
            quality["invalid_raw_event_timestamps"],
            quality["garbled_memories"],
            candidate_health["garbled_candidate_count"],
            candidate_health["duplicate_cluster_count"],
        ]
    )
    checks.append(
        {
            "name": "data_quality",
            "status": "warning" if has_quality_issues else "passed",
            "details": [
                f"invalid_stability={len(quality['invalid_stability_memories'])}",
                f"invalid_memory_timestamps={len(quality['invalid_memory_timestamps'])}",
                f"invalid_raw_event_timestamps={len(quality['invalid_raw_event_timestamps'])}",
                f"garbled_memories={len(quality['garbled_memories'])}",
                f"candidate_count={candidate_health['candidate_count']}",
                f"candidate_age_p95={candidate_health['candidate_age_p95']}",
                f"duplicate_cluster_count={candidate_health['duplicate_cluster_count']}",
            ],
        }
    )

    rendered_root = config.data_home_path / "rendered"
    rendered_ok = (rendered_root / "memory-brief.md").exists()
    checks.append(
        {
            "name": "render_outputs",
            "status": "passed" if rendered_ok else "warning",
            "details": [f"rendered_root={rendered_root}", f"memory_brief_exists={rendered_ok}"],
        }
    )

    status_counts = {"passed": 0, "warning": 0, "failed": 0}
    for check in checks:
        status_counts[check["status"]] += 1
    overall = "failed" if status_counts["failed"] else ("warning" if status_counts["warning"] else "passed")
    return {
        "summary": {
            "overall": overall,
            "passed": status_counts["passed"],
            "warning": status_counts["warning"],
            "failed": status_counts["failed"],
            "config_path": str(config.config_path),
            "data_home": str(config.data_home_path),
            "db_path": str(store.db_path),
        },
        "checks": checks,
        "quality_signals": quality,
    }
