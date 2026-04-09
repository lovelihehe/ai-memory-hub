"""
记忆管理模块。

提供：
- list_memories: 按条件列出记忆
- apply_feedback: 批量应用用户反馈
"""

from __future__ import annotations

import json

from ai_memory_hub.core.models import utc_now
from ai_memory_hub.storage.db import MemoryStore


def list_memories(
    store: MemoryStore,
    *,
    status: str | None = None,
    scope: str | None = None,
    tool: str | None = None,
    project: str | None = None,
    limit: int = 50,
) -> list[dict]:
    where_clauses = []
    params: list[object] = []
    if status and status != "all":
        where_clauses.append("status = ?")
        params.append(status)
    if scope and scope != "all":
        where_clauses.append("scope = ?")
        params.append(scope)
    if tool and tool != "all":
        where_clauses.append("tool = ?")
        params.append(tool)
    if project:
        where_clauses.append("project_key = ?")
        params.append(project)

    where_sql = f"where {' and '.join(where_clauses)}" if where_clauses else ""
    with store.connect() as conn:
        rows = conn.execute(
            f"""
            select *
            from memories
            {where_sql}
            order by
              case status
                when 'candidate' then 0
                when 'active' then 1
                when 'archived' then 2
                when 'contradicted' then 3
                else 4
              end,
              confidence desc,
              stability desc,
              coalesce(reviewed_at, last_seen_at, created_at) desc
            limit ?
            """,
            params + [limit],
        ).fetchall()

    return [
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
            "reviewed_at": row["reviewed_at"],
            "last_seen_at": row["last_seen_at"],
            "tags": json.loads(row["tags_json"]),
        }
        for row in rows
    ]


def batch_apply_feedback(
    store: MemoryStore,
    *,
    action: str,
    min_confidence: float = 0.0,
    by_age_days: int = 0,
    dry_run: bool = False,
) -> dict:
    """批量执行 review action"""
    from datetime import datetime, timedelta, timezone as tz

    candidates = list_memories(store, status="candidate", limit=1000)

    if by_age_days > 0:
        cutoff = datetime.now(tz.utc) - timedelta(days=by_age_days)
        candidates = [
            c for c in candidates
            if c.get("created_at")
        ]
        filtered = []
        for c in candidates:
            ts = str(c["created_at"]).replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(ts)
                if dt <= cutoff:
                    filtered.append(c)
            except Exception:
                filtered.append(c)
        candidates = filtered

    candidates = [c for c in candidates if c["confidence"] >= min_confidence]

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "would_apply": len(candidates),
            "action": action,
            "candidates": [{"id": c["id"], "title": c["title"], "confidence": c["confidence"]} for c in candidates[:20]],
        }

    applied = 0
    failed = 0
    total = len(candidates)
    for i, candidate in enumerate(candidates):
        result = apply_feedback(store, memory_id=candidate["id"], action=action, rebuild_index=(i == total - 1))
        if result.get("ok"):
            applied += 1
        else:
            failed += 1

    return {
        "ok": True,
        "action": action,
        "applied": applied,
        "failed": failed,
        "total_candidates_found": len(candidates),
    }


def apply_feedback(store: MemoryStore, *, memory_id: str, action: str, target_id: str | None = None, rebuild_index: bool = True) -> dict:
    record = store.load_memory(memory_id)
    if not record:
        return {"ok": False, "message": f"Memory not found: {memory_id}"}

    if action in {"promote", "confirm"}:
        record.status = "active"
        record.confidence = max(record.confidence, 0.9)
        record.stability = max(record.stability, 0.85)
    elif action == "demote":
        record.status = "candidate"
    elif action == "archive":
        record.status = "archived"
    elif action == "contradict":
        if target_id and not store.load_memory(target_id):
            return {"ok": False, "message": f"Target memory not found: {target_id}"}
        record.status = "contradicted"
        record.supersedes = target_id or record.supersedes
    elif action == "merge":
        if not target_id:
            return {"ok": False, "message": "target_id is required for merge"}
        if not store.load_memory(target_id):
            return {"ok": False, "message": f"Target memory not found: {target_id}"}
        record.status = "archived"
        record.supersedes = target_id
    else:
        return {"ok": False, "message": f"Unsupported action: {action}"}

    record.manual_override = True
    record.reviewed_at = utc_now()
    store.write_memory(record)
    if rebuild_index:
        store.rebuild_memory_index()
    return {
        "ok": True,
        "memory_id": memory_id,
        "status": record.status,
        "confidence": record.confidence,
        "stability": record.stability,
        "supersedes": record.supersedes,
        "reviewed_at": record.reviewed_at,
    }
