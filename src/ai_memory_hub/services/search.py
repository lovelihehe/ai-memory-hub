"""
搜索服务模块。

提供：
- memory_search: 混合搜索（FTS5 + 向量）
- memory_context: 任务上下文生成
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.core.utils import normalize_project_reference

logger = logging.getLogger(__name__)


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"\w+", query or "", flags=re.UNICODE) if term]


def _normalize_query(query: str) -> str:
    return " ".join(_query_terms(query))


def _build_fts_query(query: str) -> str:
    terms = _query_terms(query)
    if not terms:
        return ""
    return " OR ".join(f'"{term}"' for term in terms)


def _priority_tuple(item: dict, normalized_project: str | None, tool: str | None) -> tuple:
    item_project = item.get("project_key")
    item_tool = item.get("tool")
    item_status = item.get("status")
    if normalized_project and item_project == normalized_project and item_status == "active":
        priority = 0
    elif item_project is None and item_tool == "shared" and item_status == "active":
        priority = 1
    elif tool and tool != "all" and item_tool == tool and item_status == "active":
        priority = 2
    elif normalized_project and item_project == normalized_project and item_status == "candidate":
        priority = 3
    else:
        priority = 4
    return (
        priority,
        0 if item_tool == "shared" else 1,
        -float(item.get("confidence", 0)),
        -float(item.get("stability", 0)),
        item.get("title", ""),
    )


def _build_where_clauses(
    scope: str | None,
    normalized_project: str | None,
    tool: str | None,
) -> tuple[list[str], list[object]]:
    clauses = ["status in ('active', 'candidate')"]
    params: list[object] = []
    if scope and scope != "all":
        clauses.append("scope = ?")
        params.append(scope)
    if normalized_project:
        clauses.append("(project_key = ? or project_key is null)")
        params.append(normalized_project)
    if tool and tool != "all":
        clauses.append("(tool = ? or tool = 'shared')")
        params.append(tool)
    return clauses, params


def _row_to_memory_dict(row: sqlite3.Row, bm25_score: float | None = None) -> dict:
    row_tags = json.loads(row["tags_json"])
    result = {
        "id": row["id"],
        "title": row["title"],
        "memory_type": row["memory_type"],
        "scope": row["scope"],
        "tool": row["tool"],
        "project_key": row["project_key"],
        "summary": row["summary"],
        "confidence": row["confidence"],
        "stability": row["stability"],
        "status": row["status"],
        "tags": row_tags,
    }
    if bm25_score is not None:
        result["bm25_score"] = bm25_score
    return result


def _fts_search(
    store: MemoryStore,
    query: str,
    scope: str | None,
    project: str | None,
    tool: str | None,
    limit: int,
) -> list[dict]:
    """执行 FTS BM25 检索，返回带 bm25_score 归一化分数的结果"""
    if not query:
        return []

    normalized_project = normalize_project_reference(project)
    fts_query = _build_fts_query(query)
    where_clauses, params = _build_where_clauses(scope, normalized_project, tool)
    normalized_query = _normalize_query(query)

    if not fts_query and not normalized_query:
        return []

    with store.connect() as conn:
        rows = []
        if fts_query:
            sql = f"""
                select
                  m.*,
                  bm25(memory_fts, 4.0, 3.0, 1.5, 1.0) as rank
                from memory_fts
                join memories m on {" and ".join(where_clauses)}
                where memory_fts match ?
                order by rank, m.confidence desc
                limit ?
            """
            try:
                rows = conn.execute(sql, params + [fts_query, limit]).fetchall()
            except sqlite3.Error as e:
                logger.warning(f"FTS search failed: {e}")
                rows = []

        if not rows and normalized_query:
            like_value = f"%{normalized_query}%"
            fallback_clauses = list(where_clauses)
            rows = conn.execute(
                f"""
                select *
                from memories
                where {" and ".join(fallback_clauses)}
                  and (title like ? or summary like ? or details like ?)
                order by confidence desc, stability desc
                limit ?
                """,
                params + [like_value, like_value, like_value, limit],
            ).fetchall()

    if not rows:
        return []

    bm25_values = [float(row["rank"]) for row in rows]
    max_rank = max(bm25_values) if bm25_values else 1.0
    results = []
    for row in rows:
        rank = float(row["rank"])
        bm25_score = max(0.0, 1.0 - rank / (max_rank + 0.001))
        results.append(_row_to_memory_dict(row, bm25_score=bm25_score))
    return results


def _vector_search(
    store: MemoryStore,
    query: str,
    scope: str | None,
    project: str | None,
    tool: str | None,
    limit: int,
) -> list[dict]:
    """执行向量语义检索"""
    vector_store = store.get_vector_store()
    if vector_store is None:
        return []

    normalized_project = normalize_project_reference(project)
    filters: dict[str, object] = {"status": {"$in": ["active", "candidate"]}}
    if scope and scope != "all":
        filters["scope"] = scope
    if normalized_project:
        filters["$or"] = [{"project_key": normalized_project}, {"project_key": None}]
    if tool and tool != "all":
        tool_filter = {"$or": [{"tool": tool}, {"tool": "shared"}]}
        filters = {"$and": [filters, tool_filter]}

    try:
        results = vector_store.search_similar(query=query, limit=limit, filters=filters)
    except Exception as e:
        logger.warning(f"Vector search failed: {e}")
        return []

    if not results:
        return results

    sim_values = [float(r.get("similarity", 0.0)) for r in results]
    max_sim = max(sim_values) if sim_values else 1.0
    for r in results:
        r["similarity"] = round(float(r.get("similarity", 0.0)) / max_sim, 4)
    return results


def _hybrid_merge(
    fts: list[dict],
    vector: list[dict],
    alpha: float,
    limit: int,
    tags: list[str] | None,
    normalized_project: str | None,
    tool: str | None,
) -> list[dict]:
    """加权融合 FTS BM25 和向量相似度"""
    fts_map = {r["id"]: r for r in fts}
    vec_map = {r["id"]: r for r in vector}
    all_ids = list(set(list(fts_map.keys()) + list(vec_map.keys())))

    scored: list[dict] = []
    for mid in all_ids:
        f = fts_map.get(mid, {})
        v = vec_map.get(mid, {})

        fts_score = f.get("bm25_score", 0.0)
        vec_score = v.get("similarity", 0.0)
        hybrid_score = alpha * fts_score + (1.0 - alpha) * vec_score

        row_fields = {k: v for k, v in (f.items() if f else v.items())
                      if k not in {"bm25_score", "similarity"}}

        scored.append({
            **row_fields,
            "id": mid,
            "bm25_score": round(fts_score, 4),
            "similarity": round(vec_score, 4),
            "hybrid_score": round(hybrid_score, 4),
        })

    scored.sort(key=lambda x: _priority_tuple(x, normalized_project, tool))
    scored.sort(key=lambda x: -x.get("hybrid_score", 0.0))

    if tags:
        scored = [s for s in scored if s.get("tags") and set(tags).issubset(set(s["tags"]))]

    return scored[:limit]


def _keyword_fallback(
    store: MemoryStore,
    query: str,
    scope: str | None,
    normalized_project: str | None,
    tool: str | None,
    limit: int,
) -> list[dict]:
    """LIKE 模糊搜索；空查询时返回所有记忆（按 priority 排序）"""
    normalized_query = _normalize_query(query)
    where_clauses, params = _build_where_clauses(scope, normalized_project, tool)

    with store.connect() as conn:
        if normalized_query:
            like_value = f"%{normalized_query}%"
            rows = conn.execute(
                f"""
                select *
                from memories
                where {" and ".join(where_clauses)}
                  and (title like ? or summary like ? or details like ?)
                order by confidence desc, stability desc
                limit ?
                """,
                params + [like_value, like_value, like_value, max(limit * 3, 20)],
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                select *
                from memories
                where {" and ".join(where_clauses)}
                order by
                  case status
                    when 'active' then 0
                    when 'candidate' then 1
                    else 2
                  end,
                  confidence desc,
                  stability desc
                limit ?
                """,
                params + [max(limit * 3, 20)],
            ).fetchall()

    results = [
        {
            "id": row["id"],
            "title": row["title"],
            "memory_type": row["memory_type"],
            "scope": row["scope"],
            "tool": row["tool"],
            "project_key": row["project_key"],
            "summary": row["summary"],
            "confidence": row["confidence"],
            "stability": row["stability"],
            "status": row["status"],
            "tags": json.loads(row["tags_json"]),
        }
        for row in rows
    ]
    return sorted(results, key=lambda item: _priority_tuple(item, normalized_project, tool))


def memory_search(
    store: MemoryStore,
    *,
    query: str,
    scope: str | None = None,
    tags: list[str] | None = None,
    project: str | None = None,
    tool: str | None = None,
    limit: int = 10,
    semantic: bool = False,
) -> list[dict]:
    """
    混合搜索：默认并行执行 FTS BM25 和向量语义检索，按 hybrid_alpha 加权融合。
    --semantic 标志保留（仅影响 alpha 值，semantic=True 时提高向量权重）。
    """
    normalized_project = normalize_project_reference(project)
    alpha = store.config.scan.hybrid_alpha
    if semantic:
        alpha = max(0.0, alpha - 0.2)

    raw_limit = max(limit * 3, 20)

    fts_results = _fts_search(store, query, scope, project, tool, raw_limit)
    vec_results = _vector_search(store, query, scope, project, tool, raw_limit)

    if vec_results and fts_results:
        merged = _hybrid_merge(fts_results, vec_results, alpha, limit, tags, normalized_project, tool)
    elif vec_results:
        merged = sorted(vec_results, key=lambda x: _priority_tuple(x, normalized_project, tool))[:limit]
        if tags:
            merged = [m for m in merged if m.get("tags") and set(tags).issubset(set(m["tags"]))]
    elif fts_results:
        merged = sorted(fts_results, key=lambda x: _priority_tuple(x, normalized_project, tool))[:limit]
        if tags:
            merged = [m for m in merged if m.get("tags") and set(tags).issubset(set(m["tags"]))]
    else:
        # 完全 fallback：空查询返回所有可用记忆（按 priority 排序）
        merged = _keyword_fallback(store, query, scope, normalized_project, tool, limit)
        if tags:
            merged = [m for m in merged if m.get("tags") and set(tags).issubset(set(m["tags"]))]

    # 使用反馈写回
    if merged:
        accessed_ids = [r["id"] for r in merged]
        store.batch_update_access(accessed_ids)

    return merged


def default_memories(
    store: MemoryStore,
    *,
    scope: str | None = None,
    project: str | None = None,
    tool: str | None = None,
    limit: int = 12,
) -> list[dict]:
    normalized_project = normalize_project_reference(project)
    where_clauses = ["status = 'active'"]
    params: list[object] = []
    if scope and scope != "all":
        where_clauses.append("scope = ?")
        params.append(scope)
    if normalized_project:
        where_clauses.append("(project_key = ? or project_key is null)")
        params.append(normalized_project)
    if tool and tool != "all":
        where_clauses.append("(tool = ? or tool = 'shared')")
        params.append(tool)

    with store.connect() as conn:
        rows = conn.execute(
            f"""
            select *
            from memories
            where {" and ".join(where_clauses)}
            order by
              case memory_type
                when 'procedural' then 0
                when 'profile' then 1
                when 'semantic' then 2
                when 'episodic' then 3
                else 4
              end,
              confidence desc,
              stability desc,
              coalesce(last_seen_at, created_at) desc
            limit ?
            """,
            params + [max(limit * 3, 20)],
        ).fetchall()

    results = [
        {
            "id": row["id"],
            "title": row["title"],
            "memory_type": row["memory_type"],
            "scope": row["scope"],
            "tool": row["tool"],
            "project_key": row["project_key"],
            "summary": row["summary"],
            "confidence": row["confidence"],
            "stability": row["stability"],
            "status": row["status"],
            "tags": json.loads(row["tags_json"]),
        }
        for row in rows
    ]
    sorted_results = sorted(results, key=lambda item: _priority_tuple(item, normalized_project, tool))[:limit]

    # 使用反馈写回
    if sorted_results:
        store.batch_update_access([r["id"] for r in sorted_results])

    return sorted_results


def memory_context(
    store: MemoryStore,
    *,
    tool: str,
    repo: str | None,
    task_type: str,
    query: str,
    evolve_profile: bool = True,
) -> dict:
    normalized_project = normalize_project_reference(repo)
    matched = memory_search(
        store,
        query=query or task_type or tool,
        scope="all",
        tags=None,
        project=normalized_project,
        tool=tool,
        limit=24,
    )
    if not matched:
        matched = default_memories(store, scope="all", project=normalized_project, tool=tool, limit=24)

    def prioritize(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda item: _priority_tuple(item, normalized_project, tool))

    must_follow = prioritize([item for item in matched if item["memory_type"] == "procedural" and item["confidence"] >= 0.82])
    preferences = prioritize([item for item in matched if item["memory_type"] == "profile"])
    known_patterns = prioritize([item for item in matched if item["memory_type"] == "semantic"])
    watch_outs = prioritize([item for item in matched if "watchout" in item["tags"]])
    related_episodes = prioritize([item for item in matched if item["memory_type"] == "episodic"])

    # 使用反馈写回（memory_search 已写过，这里对 default_memories 补充）
    all_ids = list({item["id"] for item in matched})
    if all_ids:
        store.batch_update_access(all_ids)

    # 用户画像自动进化
    if evolve_profile:
        _trigger_profile_evolution(tool, task_type, query, normalized_project)

    return {
        "must_follow": must_follow[:8],
        "preferences": preferences[:8],
        "known_patterns": known_patterns[:8],
        "watch_outs": watch_outs[:8],
        "related_episodes": related_episodes[:8],
    }


def _trigger_profile_evolution(
    tool: str,
    task_type: str,
    query: str,
    project: str | None,
) -> None:
    """在后台触发用户画像的辩证进化。"""
    try:
        from ai_memory_hub.core.config import load_config
        from ai_memory_hub.services.profile_service import get_profile_service
        config = load_config()
        if not config.learning.enabled or not config.learning.profile_update_on_context:
            return
        profile_service = get_profile_service(config)
        profile_service.record_context_use()
        profile_service.record_tool_usage(tool)
        if project:
            profile_service._ensure_profile().add_project(project)
        if task_type:
            profile_service._ensure_profile().add_expertise(task_type)
    except Exception:
        pass
