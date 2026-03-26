"""
成长趋势分析模块。

分析记忆库的成长情况，支持 week / month / quarter 三个时间窗口。
返回摘要、按类型/范围分组、TOP 使用记忆和里程碑信息。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ai_memory_hub.core.config import load_config
from ai_memory_hub.core.models import MemoryRecord, utc_now
from ai_memory_hub.storage.db import MemoryStore


def memory_growth(store: MemoryStore, *, period: str = "week") -> dict:
    """
    成长趋势分析，支持 week / month / quarter 三个时间窗口。
    返回：summary、by_type、by_scope、top_used_memories、milestone 五个分组。
    """
    now = datetime.now(timezone.utc)
    if period == "week":
        window = timedelta(days=7)
        label = _week_label(now)
    elif period == "month":
        window = timedelta(days=30)
        label = _month_label(now)
    elif period == "quarter":
        window = timedelta(days=90)
        label = _quarter_label(now)
    else:
        window = timedelta(days=7)
        label = _week_label(now)

    cutoff = now - window
    records = _load_all_records(store)

    active = [r for r in records if r.status == "active"]
    recent_active = [r for r in active if _record_dt(r) >= cutoff]
    ever_used = [r for r in active if r.last_accessed_at is not None]
    frequently_used = sorted(ever_used, key=lambda r: _usage_score(r), reverse=True)[:10]

    by_type = _group_by(active, lambda r: r.memory_type)
    by_scope = _group_by(active, lambda r: r.scope)

    milestone = _check_milestone(len(active))

    return {
        "period": period,
        "label": label,
        "window_days": window.days,
        "summary": {
            "total_active_memories": len(active),
            "new_this_period": len(recent_active),
            "ever_used": len(ever_used),
            "usage_rate": round(len(ever_used) / len(active), 3) if active else 0,
            "avg_confidence": round(sum(r.confidence for r in active) / len(active), 3) if active else 0,
            "avg_stability": round(sum(r.stability for r in active) / len(active), 3) if active else 0,
        },
        "by_type": {k: len(v) for k, v in by_type.items()},
        "by_scope": {k: len(v) for k, v in by_scope.items()},
        "top_used_memories": [
            {
                "id": r.id,
                "title": r.title,
                "memory_type": r.memory_type,
                "usage_count": r.usage_count or 0,
                "last_accessed_at": r.last_accessed_at,
            }
            for r in frequently_used
        ],
        "milestone": milestone,
    }


def _load_all_records(store: MemoryStore) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for path in store.iter_memory_files():
        try:
            record = MemoryRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            records.append(record)
        except Exception:
            continue
    return records


def _record_dt(r: MemoryRecord) -> datetime:
    ts = r.last_seen_at or r.reviewed_at or r.created_at
    ts = str(ts).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _usage_score(r: MemoryRecord) -> int:
    return (r.usage_count or 0) * 10 + (1 if r.last_accessed_at else 0)


def _group_by(items: list, key_fn) -> dict:
    result: dict = defaultdict(list)
    for item in items:
        result[key_fn(item)].append(item)
    return dict(result)


def _check_milestone(total_active: int) -> dict | None:
    MILESTONES = [10, 25, 50, 100, 200, 500]
    reached = [m for m in MILESTONES if total_active >= m]
    if not reached:
        return None
    return {
        "reached": max(reached),
        "next": next((m for m in MILESTONES if m > total_active), None),
        "progress_pct": min(100, round(total_active / (reached[-1] + 50) * 100, 1)),
    }


def _week_label(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _month_label(dt: datetime) -> str:
    return f"{dt.year}-{dt.month:02d}"


def _quarter_label(dt: datetime) -> str:
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}-Q{q}"
