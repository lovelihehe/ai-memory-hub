"""
统计分析模块。

生成记忆库的统计报告，包括：
- 按状态/类型/范围/工具分组统计
- TOP 最长详情、TOP 最新记忆
- 数据质量信号
"""

from __future__ import annotations

import json
from collections import Counter

from ai_memory_hub.extraction.quality import collect_memory_quality_signals
from ai_memory_hub.storage.db import MemoryStore


def memory_stats(store: MemoryStore, *, top_n: int = 10) -> dict:
    with store.connect() as conn:
        raw_event_count = int(conn.execute("select count(*) from raw_events").fetchone()[0])
        rows = conn.execute(
            """
            select
              id,
              title,
              memory_type,
              scope,
              tool,
              confidence,
              stability,
              status,
              tags_json,
              details,
              created_at,
              last_seen_at,
              reviewed_at
            from memories
            """
        ).fetchall()
        raw_events_by_tool = _group_counts(conn, "select source_tool, count(*) as c from raw_events group by source_tool order by c desc")

    memories = [dict(row) for row in rows]
    quality = collect_memory_quality_signals(store)
    candidate_health = quality["candidate_health"]
    total_memories = len(memories)
    memories_by_status = _counter(memories, "status")
    memories_by_type = _counter(memories, "memory_type")
    memories_by_scope = _counter(memories, "scope")
    memories_by_tool = _counter(memories, "tool")
    shared_count = sum(1 for item in memories if item["tool"] == "shared")
    tag_counter: Counter[str] = Counter()
    for item in memories:
        for tag in json.loads(item["tags_json"]):
            tag_counter[tag] += 1

    longest_details = [
        {
            "id": item["id"],
            "title": item["title"],
            "tool": item["tool"],
            "status": item["status"],
            "detail_length": len(item["details"] or ""),
        }
        for item in sorted(memories, key=lambda current: len(current["details"] or ""), reverse=True)[:top_n]
    ]
    recent_memories = [
        {
            "id": item["id"],
            "title": item["title"],
            "tool": item["tool"],
            "status": item["status"],
            "last_seen_at": item["last_seen_at"],
            "created_at": item["created_at"],
        }
        for item in sorted(
            memories,
            key=lambda current: (current["last_seen_at"] or current["created_at"] or ""),
            reverse=True,
        )[:top_n]
    ]
    low_confidence = [
        {
            "id": item["id"],
            "title": item["title"],
            "tool": item["tool"],
            "confidence": item["confidence"],
            "status": item["status"],
        }
        for item in memories
        if item["confidence"] < 0.78
    ]
    return {
        "summary": {
            "raw_event_count": raw_event_count,
            "memory_count": total_memories,
            "memory_conversion_ratio": round(total_memories / raw_event_count, 6) if raw_event_count else 0,
            "shared_memory_ratio": round(shared_count / total_memories, 4) if total_memories else 0,
            "avg_confidence": round(sum(item["confidence"] for item in memories) / total_memories, 4) if total_memories else 0,
            "avg_stability": round(sum(item["stability"] for item in memories) / total_memories, 4) if total_memories else 0,
            "candidate_count": candidate_health["candidate_count"],
            "candidate_age_p95": candidate_health["candidate_age_p95"],
            "garbled_candidate_count": candidate_health["garbled_candidate_count"],
            "duplicate_cluster_count": candidate_health["duplicate_cluster_count"],
            "stats_may_be_skewed": any(
                [
                    quality["invalid_stability_memories"],
                    quality["invalid_memory_timestamps"],
                    quality["invalid_raw_event_timestamps"],
                    quality["garbled_memories"],
                ]
            ),
            "stats_warning": (
                "Statistics are affected by invalid memory data. Run `ai-memory repair-data`."
                if any(
                    [
                        quality["invalid_stability_memories"],
                        quality["invalid_memory_timestamps"],
                        quality["invalid_raw_event_timestamps"],
                        quality["garbled_memories"],
                    ]
                )
                else None
            ),
        },
        "distributions": {
            "memories_by_status": memories_by_status,
            "memories_by_type": memories_by_type,
            "memories_by_scope": memories_by_scope,
            "memories_by_tool": memories_by_tool,
            "raw_events_by_tool": raw_events_by_tool,
            "top_tags": [{"name": name, "count": count} for name, count in tag_counter.most_common(top_n)],
        },
        "quality_signals": {
            "low_confidence_memories": low_confidence[:top_n],
            "invalid_stability_memories": quality["invalid_stability_memories"][:top_n],
            "invalid_memory_timestamps": quality["invalid_memory_timestamps"][:top_n],
            "invalid_raw_event_timestamps": quality["invalid_raw_event_timestamps"][:top_n],
            "garbled_memories": quality["garbled_memories"][:top_n],
            "candidate_health": candidate_health,
            "longest_details": longest_details,
            "recent_memories": recent_memories,
        },
    }


def _counter(memories: list[dict], key: str) -> list[dict]:
    counts = Counter(item[key] for item in memories)
    return [{"name": name, "count": count} for name, count in counts.most_common()]


def _group_counts(conn, sql: str) -> list[dict]:
    return [{"name": row[0], "count": row[1]} for row in conn.execute(sql).fetchall()]
