"""
数据质量治理模块。

负责：
- 候选记忆的自动审核（promote / archive）
- 乱码、重复、低价值记忆的检测和修复
- 时间戳规范化
- 矛盾检测和里程碑追踪
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from ai_memory_hub.extraction.extractors import consolidate, is_low_value_text
from ai_memory_hub.core.models import MemoryRecord
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.core.utils import contains_mojibake, trim_excerpt

MEMORY_TIMESTAMP_FIELDS = ("created_at", "last_seen_at", "reviewed_at")
LOW_VALUE_MARKERS = (
    "please implement this plan",
    "this skill should be used",
    "available skills",
    "skill.md",
    "帮我",
    "请帮我",
    "怎么做",
    "分析一下",
    "继续",
    "还有啥",
)


def normalize_timestamp(value: str | None) -> tuple[str | None, bool, str | None]:
    if value in (None, ""):
        return value, False, None
    if isinstance(value, str) and value.isdigit():
        try:
            timestamp = int(value)
            if len(value) >= 13:
                normalized = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
            else:
                normalized = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            return normalized, normalized != value, None
        except (OverflowError, ValueError, OSError):
            return value, False, "unparseable_unix_timestamp"
    try:
        normalized = datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
        return normalized, normalized != value, None
    except ValueError:
        return value, False, "unparseable_timestamp"


def _is_low_value_candidate(payload: dict) -> bool:
    text = " ".join(str(payload.get(field, "") or "") for field in ("title", "summary", "details"))
    lowered = text.lower()
    if not lowered:
        return True
    if contains_mojibake(text):
        return True
    if is_low_value_text(text):
        return True
    if any(marker in lowered for marker in LOW_VALUE_MARKERS):
        return True
    if len((payload.get("title") or "").strip()) < 8:
        return True
    if lowered.count("\\") + lowered.count("/") >= 4 and len(lowered.split()) <= 8:
        return True
    return False


def _candidate_duplicate_groups(candidates: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str | None], list[dict]] = {}
    for payload in candidates:
        key = (trim_excerpt((payload.get("summary") or "").lower(), 96), payload.get("project_key"))
        grouped.setdefault(key, []).append(payload)
    return [
        {
            "summary_key": key[0],
            "project_key": key[1],
            "count": len(items),
            "memory_ids": [item["id"] for item in items],
        }
        for key, items in grouped.items()
        if len(items) > 1 and key[0]
    ]


def collect_candidate_health_metrics(store: MemoryStore) -> dict:
    candidates: list[dict] = []
    ages: list[float] = []
    garbled = 0
    low_value = 0
    for path in store.iter_memory_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "candidate":
            continue
        candidates.append(payload)
        created_at = payload.get("created_at")
        normalized, _, _ = normalize_timestamp(created_at)
        if normalized:
            age_seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(normalized)).total_seconds()
            ages.append(max(0.0, age_seconds / 86400))
        if any(contains_mojibake(payload.get(field)) for field in ("title", "summary", "details")):
            garbled += 1
        if _is_low_value_candidate(payload):
            low_value += 1

    duplicate_groups = _candidate_duplicate_groups(candidates)
    return {
        "candidate_count": len(candidates),
        "candidate_age_p95": round(statistics.quantiles(ages, n=20)[-1], 2) if len(ages) >= 2 else round(ages[0], 2) if ages else 0.0,
        "garbled_candidate_count": garbled,
        "low_value_candidate_count": low_value,
        "duplicate_cluster_count": len(duplicate_groups),
        "duplicate_clusters": duplicate_groups[:10],
    }


def collect_memory_quality_signals(store: MemoryStore) -> dict[str, list[dict] | dict]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            select id, title, summary, details, tool, status, stability, created_at, last_seen_at, reviewed_at
            from memories
            """
        ).fetchall()
        raw_rows = conn.execute(
            """
            select id, source_tool, source_path, timestamp
            from raw_events
            """
        ).fetchall()

    invalid_stability = []
    invalid_memory_timestamps = []
    invalid_raw_event_timestamps = []
    garbled_memories = []
    for row in rows:
        if row["stability"] < 0 or row["stability"] > 1:
            invalid_stability.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "tool": row["tool"],
                    "stability": row["stability"],
                    "status": row["status"],
                }
            )
        for field in MEMORY_TIMESTAMP_FIELDS:
            _, _, error = normalize_timestamp(row[field])
            if error:
                invalid_memory_timestamps.append(
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "field": field,
                        "value": row[field],
                        "status": row["status"],
                    }
                )
        if row["status"] != "archived" and any(contains_mojibake(row[field]) for field in ("title", "summary", "details")):
            garbled_memories.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "tool": row["tool"],
                    "status": row["status"],
                }
            )

    for row in raw_rows:
        _, _, error = normalize_timestamp(row["timestamp"])
        if error:
            invalid_raw_event_timestamps.append(
                {
                    "id": row["id"],
                    "source_tool": row["source_tool"],
                    "source_path": row["source_path"],
                    "value": row["timestamp"],
                }
            )

    return {
        "invalid_stability_memories": invalid_stability,
        "invalid_memory_timestamps": invalid_memory_timestamps,
        "invalid_raw_event_timestamps": invalid_raw_event_timestamps,
        "garbled_memories": garbled_memories,
        "candidate_health": collect_candidate_health_metrics(store),
    }


def govern_candidates(store: MemoryStore) -> dict[str, int]:
    promoted = 0
    archived = 0
    needs_review = 0

    candidates: list[tuple[dict, str]] = []
    for path in store.iter_memory_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "candidate":
            continue
        candidates.append((payload, str(path)))

    duplicate_clusters = _candidate_duplicate_groups([payload for payload, _ in candidates])
    duplicate_ids = {memory_id for cluster in duplicate_clusters for memory_id in cluster["memory_ids"][1:]}

    for payload, file_path in candidates:
        changed = False
        tags = [item for item in payload.get("tags", []) if isinstance(item, str)]
        if payload.get("id") in duplicate_ids:
            payload["status"] = "archived"
            payload["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            if "auto-merged-candidate" not in tags:
                tags.append("auto-merged-candidate")
            archived += 1
            changed = True
        elif _is_low_value_candidate(payload):
            payload["status"] = "archived"
            payload["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            if "low-value-archived" not in tags:
                tags.append("low-value-archived")
            archived += 1
            changed = True
        else:
            confidence = float(payload.get("confidence", 0))
            stability = float(payload.get("stability", 0))
            requires_review = "needs-review" in tags or payload.get("scope") == "project" or payload.get("memory_type") == "profile"
            if confidence >= store.config.scan.auto_activate_confidence and stability >= store.config.scan.auto_activate_stability and not requires_review:
                payload["status"] = "active"
                payload["reviewed_at"] = datetime.now(timezone.utc).isoformat()
                if "auto-promoted" not in tags:
                    tags.append("auto-promoted")
                promoted += 1
                changed = True
            elif requires_review:
                needs_review += 1
                if "needs-review" not in tags:
                    tags.append("needs-review")
                    changed = True

        if changed:
            payload["tags"] = sorted(set(tags))
            with open(file_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)

    return {
        "promoted_candidates": promoted,
        "archived_candidates": archived,
        "needs_review_candidates": needs_review,
        "duplicate_cluster_count": len(duplicate_clusters),
    }


def repair_data(store: MemoryStore) -> dict:
    memory_changes: list[dict] = []
    skipped: list[dict] = []
    repaired_memory_files = 0
    removed_system_generated = 0
    archived_manual_memories = 0
    for path in store.iter_memory_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        changed = False

        has_garbled_text = any(contains_mojibake(payload.get(field)) for field in ("title", "summary", "details"))
        if has_garbled_text:
            if payload.get("manual_override"):
                before_status = payload.get("status")
                payload["status"] = "archived"
                payload["reviewed_at"] = datetime.now(timezone.utc).isoformat()
                tags = [item for item in payload.get("tags", []) if isinstance(item, str)]
                if "garbled-archived" not in tags:
                    tags.append("garbled-archived")
                payload["tags"] = tags
                memory_changes.append(
                    {
                        "kind": "garbled_manual_archived",
                        "id": payload.get("id"),
                        "file_path": str(path),
                        "before": before_status,
                        "after": "archived",
                    }
                )
                archived_manual_memories += 1
                changed = True
            else:
                path.unlink(missing_ok=True)
                memory_changes.append({"kind": "garbled_system_removed", "id": payload.get("id"), "file_path": str(path)})
                removed_system_generated += 1
                continue

        stability = payload.get("stability")
        if isinstance(stability, (int, float)) and (stability < 0 or stability > 1):
            new_value = max(0.0, min(1.0, float(stability)))
            memory_changes.append(
                {
                    "kind": "memory_stability",
                    "id": payload.get("id"),
                    "file_path": str(path),
                    "field": "stability",
                    "before": stability,
                    "after": new_value,
                }
            )
            payload["stability"] = new_value
            changed = True

        for field in MEMORY_TIMESTAMP_FIELDS:
            normalized, field_changed, error = normalize_timestamp(payload.get(field))
            if field_changed:
                memory_changes.append(
                    {
                        "kind": "memory_timestamp",
                        "id": payload.get("id"),
                        "file_path": str(path),
                        "field": field,
                        "before": payload.get(field),
                        "after": normalized,
                    }
                )
                payload[field] = normalized
                changed = True
            elif error:
                skipped.append(
                    {
                        "kind": "memory_timestamp",
                        "id": payload.get("id"),
                        "file_path": str(path),
                        "field": field,
                        "value": payload.get(field),
                        "reason": error,
                    }
                )

        if changed:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            repaired_memory_files += 1

    raw_event_changes: list[dict] = []
    with store.connect() as conn:
        rows = conn.execute("select id, timestamp from raw_events").fetchall()
        for row in rows:
            normalized, changed, error = normalize_timestamp(row["timestamp"])
            if changed:
                conn.execute("update raw_events set timestamp = ? where id = ?", (normalized, row["id"]))
                raw_event_changes.append(
                    {
                        "kind": "raw_event_timestamp",
                        "id": row["id"],
                        "field": "timestamp",
                        "before": row["timestamp"],
                        "after": normalized,
                    }
                )
            elif error:
                skipped.append(
                    {
                        "kind": "raw_event_timestamp",
                        "id": row["id"],
                        "field": "timestamp",
                        "value": row["timestamp"],
                        "reason": error,
                    }
                )
        conn.commit()

    consolidate_stats = {"memories_written": 0, "active_memories": 0, "queued_for_review": 0}
    if removed_system_generated:
        consolidate_stats = consolidate(store.config, store)
    candidate_governance = govern_candidates(store)
    indexed_memories = store.rebuild_memory_index(incremental=False)
    return {
        "ok": True,
        "memory_files_repaired": repaired_memory_files,
        "raw_events_repaired": len(raw_event_changes),
        "removed_system_generated": removed_system_generated,
        "archived_manual_memories": archived_manual_memories,
        "indexed_memories": indexed_memories,
        "reconsolidated_memories": consolidate_stats["memories_written"],
        **candidate_governance,
        "changes": (memory_changes + raw_event_changes),
        "skipped": skipped,
    }


def _load_active_records(store: MemoryStore) -> list[MemoryRecord]:
    """加载所有 active 记忆（供冲突检测和里程碑使用）"""
    records: list = []
    for path in store.iter_memory_files():
        try:
            record = MemoryRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if record.status == "active":
                records.append(record)
        except Exception:
            continue
    return records


def detect_contradictions(store: MemoryStore) -> dict[str, int]:
    """检测候选记忆中的潜在矛盾，返回冲突统计"""
    from ai_memory_hub.extraction.llm_analysis import detect_contradiction
    from ai_memory_hub.core.models import utc_now

    active = [r for r in _load_active_records(store)
              if r.memory_type in {"profile", "procedural"}]

    conflicts = 0
    checked = 0
    for i, a in enumerate(active):
        for b in active[i+1:]:
            if a.memory_type != b.memory_type:
                continue
            if a.scope != b.scope:
                continue
            checked += 1
            result = detect_contradiction(
                memory_a={"title": a.title, "summary": a.summary},
                memory_b={"title": b.title, "summary": b.summary},
            )
            if result is True:
                _tag_contradiction(store, a, b)
                conflicts += 1

    return {"contradictions_checked": checked, "conflicts_found": conflicts}


def _tag_contradiction(store: MemoryStore, a: MemoryRecord, b: MemoryRecord) -> None:
    """为两条矛盾记忆打上 potential-conflict 标签"""
    for record in [a, b]:
        if "potential-conflict" not in record.tags:
            record.tags = sorted(record.tags + ["potential-conflict"])
            store.write_memory(record)


def check_milestones(store: MemoryStore) -> dict:
    """检测里程碑达成并写入 Obsidian"""
    from ai_memory_hub.core.config import load_config
    from ai_memory_hub.core.models import utc_now

    config = load_config()
    active_count = len(_load_active_records(store))

    MILESTONES = [10, 25, 50, 100, 200, 500]
    reached = [m for m in MILESTONES if active_count >= m]
    if not reached:
        return {"milestone_reached": None}

    latest = reached[-1]
    state_path = store.root / "state" / "milestones.json"
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text("utf-8"))

    if str(latest) in state:
        return {"milestone_reached": latest, "already_recorded": True}

    if config.obsidian.enabled:
        try:
            from ai_memory_hub.services.obsidian import ensure_vault_layout
            vault_root = Path(config.obsidian.vault_root)
            ensure_vault_layout(config)
            rules_dir = vault_root / config.obsidian.rules_dir
            rules_dir.mkdir(parents=True, exist_ok=True)
            milestone_note = rules_dir / f"_里程碑_{latest}条记忆.md"
            content = (
                f"# 里程碑：{latest} 条 active 记忆\n\n"
                f"- 达成时间：{utc_now()}\n"
                f"- 当前 active 总数：{active_count}\n"
                f"- 感谢你的持续整理和 review\n"
            )
            milestone_note.write_text(content, encoding="utf-8")
        except Exception:
            pass

    state[str(latest)] = utc_now()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "milestone_reached": latest,
        "next": next((m for m in MILESTONES if m > active_count), None),
    }
