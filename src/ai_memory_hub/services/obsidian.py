"""
Obsidian 同步模块。

将记忆同步到 Obsidian Vault，支持：
- 按类型/项目路由到不同目录
- LLM 辅助的标题优化和去重
- 收件箱定期迁移
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.extraction.llm_analysis import grounded_keep_best, grounded_route_decision, grounded_title
from ai_memory_hub.core.models import MemoryRecord
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.core.utils import contains_mojibake, ensure_parent, project_key_from_path, stable_id, trim_excerpt


HIGH_VALUE_MARKERS = (
    "需求",
    "目标",
    "success criteria",
    "计划",
    "方案",
    "步骤",
    "里程碑",
    "decision",
    "decide",
    "决策",
    "取舍",
    "复盘",
    "总结",
    "教训",
    "踩坑",
    "规则",
    "默认",
    "以后",
    "必须",
    "不要",
    "优先",
    "always",
    "never",
    "prefer",
)
LOW_VALUE_MARKERS = (
    "available skills",
    "skill.md",
    "<instructions>",
    "please implement this plan",
    "help me build",
    "this skill should be used",
)
LESSON_NOISE_MARKERS = (
    "<proposed_plan>",
    "available skills",
    "this skill should be used",
    "please implement this plan",
    "我先",
    "我会",
    "我准备",
    "接下来",
    "先检查",
    "先确认",
    "我在",
    "我刚",
    "已经补上",
    "现在先",
    "测试没跑出来",
)
LESSON_SIGNAL_MARKERS = (
    "复盘",
    "教训",
    "经验",
    "风险",
    "原因",
    "根因",
    "避免",
    "下次",
    "应该",
    "不要",
    "必须",
    "默认",
    "结论",
    "tradeoff",
    "decision",
    "lesson",
    "risk",
    "root cause",
    "avoid",
)
TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("复盘", ("复盘", "总结", "教训", "踩坑", "postmortem", "retro", "retrospective", "lessons learned")),
    ("计划", ("计划", "方案", "实施", "步骤", "milestone", "roadmap", "plan", "rollout")),
    ("需求", ("需求", "目标", "约束", "success criteria", "need", "must have", "requirement")),
    ("规则", ("默认", "以后", "必须", "不要", "优先", "always", "never", "prefer", "rule")),
    ("决策", ("决策", "决定", "取舍", "tradeoff", "decision", "choose", "chosen", "adopt")),
)
PROJECT_SUBDIRS = {
    "需求": "01-需求",
    "计划": "02-计划",
    "决策": "03-决策",
    "复盘": "04-复盘",
}
FRONTMATTER_ORDER = (
    "project",
    "project_key",
    "type",
    "status",
    "source",
    "tags",
    "created",
    "updated",
    "confidence",
)


@dataclass(slots=True)
class VaultNote:
    note_id: str
    title: str
    note_type: str
    project_name: str | None
    project_key: str | None
    status: str
    source: str
    tags: list[str]
    created: str
    updated: str
    confidence: float
    body: str
    route: str
    source_ref: str


def sync_obsidian_vault(config: MemoryConfig, store: MemoryStore) -> dict[str, int | str]:
    if not config.obsidian.enabled:
        return {"obsidian_enabled": 0, "vault_notes_written": 0, "vault_pending_notes": 0}

    vault_root = Path(config.obsidian.vault_root)
    ensure_vault_layout(config)
    state = _load_state(store)

    conversation_notes = _collect_conversation_notes(config, store)
    rule_notes = _collect_rule_notes(config, store)
    notes = conversation_notes + rule_notes

    written = 0
    seen_ids: set[str] = set()
    for note in notes:
        state_key = note.note_id
        current_path_value = state.get(state_key, {}).get("path")
        current_path = Path(current_path_value) if current_path_value else None
        desired_path = _target_path(config, vault_root, note)
        final_path = _resolve_path(desired_path, current_path)
        _write_note(final_path, note)
        state[state_key] = {"path": str(final_path), "updated": note.updated, "route": note.route, "source_ref": note.source_ref}
        written += 1
        seen_ids.add(state_key)

    for note_id in list(state.keys()):
        if note_id not in seen_ids and not Path(state[note_id]["path"]).exists():
            state.pop(note_id, None)

    _write_supporting_docs(config, vault_root)
    weekly_updates = _write_weekly_summaries(config, vault_root, store, notes)
    pending = _write_pending_list(config, vault_root)
    vault_imported = _import_new_obsidian_notes(config, store)
    _save_state(store, state)
    return {
        "obsidian_enabled": 1,
        "vault_root": str(vault_root),
        "vault_notes_written": written,
        "vault_weekly_updates": weekly_updates,
        "vault_pending_notes": pending,
        "vault_imported": vault_imported,
    }


def ensure_vault_layout(config: MemoryConfig) -> Path:
    vault_root = Path(config.obsidian.vault_root)
    for folder in (
        config.obsidian.inbox_dir,
        config.obsidian.projects_dir,
        config.obsidian.rules_dir,
        config.obsidian.reviews_dir,
        config.obsidian.archive_dir,
    ):
        (vault_root / folder).mkdir(parents=True, exist_ok=True)
    return vault_root


def _collect_conversation_notes(config: MemoryConfig, store: MemoryStore) -> list[VaultNote]:
    grouped: dict[tuple[str, str, str, str], VaultNote] = {}
    for event in store.list_raw_events():
        role = event["role"] or "user"
        if role not in config.obsidian.capture_roles:
            continue
        text = (event["text"] or "").strip()
        if not _is_high_value_text(text):
            continue
        note_type = _classify_note_type(text)
        if note_type is None:
            continue
        project_name = _project_name(event["cwd"], event["project_key"])
        confidence = _event_confidence(text=text, note_type=note_type, has_project=project_name is not None, role=role)
        route = grounded_route_decision(
            content=text,
            current_type=note_type,
            project_name=project_name,
            fallback_route=_route_for_conversation(config, note_type=note_type, project_name=project_name, confidence=confidence),
        )
        title = grounded_title(
            title=f"{note_type}候选标题",
            content=text,
            fallback=_extract_title(text, note_type),
            max_length=48,
        )
        created = _normalize_timestamp(event["timestamp"])
        day = created[:10]
        dedupe_key = (day, note_type, project_name or "", title.lower())
        source_label = f"{event['source_tool']}:{role}"
        body_block = _conversation_body(event=event, title=title, note_type=note_type, confidence=confidence)
        tags = sorted({event["source_tool"], role, note_type, *(["高价值"] if confidence >= config.obsidian.direct_route_confidence else ["待整理"])})

        existing = grouped.get(dedupe_key)
        if existing is None:
            grouped[dedupe_key] = VaultNote(
                note_id=stable_id("obsidian-conversation", day, note_type, project_name or "", title.lower()),
                title=title,
                note_type=note_type,
                project_name=project_name,
                project_key=event["project_key"] or project_key_from_path(event["cwd"]),
                status="draft" if route != "inbox" else "inbox",
                source=source_label,
                tags=tags,
                created=created,
                updated=created,
                confidence=confidence,
                body=body_block,
                route=route,
                source_ref=event["id"],
            )
            continue

        existing.updated = max(existing.updated, created)
        existing.confidence = round(max(existing.confidence, confidence), 3)
        existing.tags = sorted(set(existing.tags) | set(tags))
        if body_block not in existing.body:
            existing.body = f"{existing.body}\n\n---\n\n{body_block}"
    return sorted(grouped.values(), key=lambda item: (item.created, item.title))


def _collect_rule_notes(config: MemoryConfig, store: MemoryStore) -> list[VaultNote]:
    # 第一步：按内容哈希去重，避免同一规则因标题差异被多次写入长期规则目录
    content_key_to_record: dict[str, MemoryRecord] = {}
    for path in store.iter_memory_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        record = MemoryRecord.from_dict(payload)
        if record.status != "active":
            continue
        if record.scope == "project":
            continue
        if record.memory_type not in {"procedural", "profile"}:
            continue
        if "needs-review" in record.tags:
            continue
        content_key = hashlib.sha1(
            f"{record.title}||{record.summary}||{record.details}".encode("utf-8")
        ).hexdigest()[:24]
        if content_key in content_key_to_record:
            existing = content_key_to_record[content_key]
            if record.created_at > existing.created_at:
                content_key_to_record[content_key] = record
        else:
            content_key_to_record[content_key] = record

    # 第二步：从去重后的记录生成 VaultNote，统一调用一次 grounded_title
    raw_notes: list[VaultNote] = []
    for record in content_key_to_record.values():
        note_type = "规则"
        title = grounded_title(
            title="长期规则标题",
            content=f"{record.title}\n{record.summary}\n{record.details}",
            fallback=_extract_title(record.title, note_type),
            max_length=48,
        )
        updated = record.last_seen_at or record.reviewed_at or record.created_at
        body = _rule_body(record)
        raw_notes.append(
            VaultNote(
                note_id=stable_id("obsidian-rule", record.id),
                title=title,
                note_type=note_type,
                project_name=None,
                project_key=None,
                status="active",
                source=f"ai-memory:{record.tool}",
                tags=sorted(set(record.tags) | {"长期规则", record.memory_type}),
                created=record.created_at,
                updated=updated,
                confidence=record.confidence,
                body=body,
                route="rules",
                source_ref=record.id,
            )
        )

    # 第三步：LLM 精选，控制输出数量
    keep_titles = grounded_keep_best(
        title="长期规则精选",
        candidates=[note.title for note in raw_notes],
        max_items=24,
    )
    keep_set = {title.lower() for title in keep_titles}
    if not keep_set:
        return raw_notes
    return [note for note in raw_notes if note.title.lower() in keep_set]


def _is_high_value_text(text: str) -> bool:
    lowered = text.lower().strip()
    if len(lowered) < 24:
        return False
    if contains_mojibake(text):
        return False
    if any(marker in lowered for marker in LOW_VALUE_MARKERS):
        return False
    if text.count("\n") >= 3:
        return True
    return any(marker in lowered for marker in HIGH_VALUE_MARKERS)


def _classify_note_type(text: str) -> str | None:
    lowered = text.lower()
    scored: list[tuple[int, int, str]] = []
    for index, (note_type, markers) in enumerate(TYPE_RULES):
        score = sum(1 for marker in markers if marker in lowered)
        if score:
            scored.append((score, -index, note_type))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


def _event_confidence(*, text: str, note_type: str, has_project: bool, role: str) -> float:
    score = 0.55
    lowered = text.lower()
    if len(text) >= 120:
        score += 0.08
    if text.count("\n") >= 3:
        score += 0.12
    if any(token in lowered for token in ("summary", "key changes", "test plan", "assumptions", "背景", "目标", "下一步")):
        score += 0.12
    if note_type in {"计划", "决策", "复盘"}:
        score += 0.08
    if has_project:
        score += 0.08
    if role == "assistant":
        score += 0.04
    return round(min(score, 0.98), 3)


def _route_for_conversation(config: MemoryConfig, *, note_type: str, project_name: str | None, confidence: float) -> str:
    if note_type == "规则":
        return "inbox"
    if project_name and confidence >= config.obsidian.direct_route_confidence:
        return "project"
    return "inbox"


def _project_name(cwd: str | None, project_key: str | None) -> str | None:
    if cwd:
        path = Path(cwd)
        if path.name:
            return _sanitize_folder_name(path.name)
    if project_key:
        return _sanitize_folder_name(project_key.rsplit("-", 1)[0])
    return None


def _sanitize_folder_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", value).strip().strip(".")
    return cleaned or "未命名项目"


def _sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", cleaned)
    cleaned = cleaned.rstrip(".")
    return cleaned[:80] or "未命名"


def _extract_title(text: str, note_type: str) -> str:
    lines = [line.strip(" #-*") for line in text.splitlines() if line.strip()]
    generic_titles = {note_type.lower(), "summary", "key changes", "test plan", "assumptions"}
    for line in lines:
        candidate = re.sub(r"^(please implement this plan:|总结：|计划：|需求：|决策：|复盘：|结论：)\s*", "", line, flags=re.IGNORECASE).strip()
        lowered = candidate.lower()
        if not candidate:
            continue
        if lowered in generic_titles:
            continue
        return trim_excerpt(candidate, 60)
    return f"{note_type}记录"


def _normalize_timestamp(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    text = str(value).strip()
    if text.endswith("Z"):
        return text[:-1] + "+00:00"
    return text


def _conversation_body(*, event, title: str, note_type: str, confidence: float) -> str:
    summary = trim_excerpt(event["text"], 180)
    content = (event["text"] or "").strip()
    created = _normalize_timestamp(event["timestamp"])
    return (
        f"# {title}\n\n"
        "## 摘要\n"
        f"{summary}\n\n"
        "## 内容\n"
        f"{content}\n\n"
        "## 来源\n"
        f"- 工具: {event['source_tool']}\n"
        f"- 角色: {event['role']}\n"
        f"- 会话: {event['session_id'] or 'unknown'}\n"
        f"- 时间: {created}\n"
        f"- 工作目录: {event['cwd'] or 'unknown'}\n"
        f"- 置信度: {confidence}\n"
    )


def _rule_body(record: MemoryRecord) -> str:
    evidence_lines = [
        f"- {item.source_tool}: {trim_excerpt(item.excerpt, 120)}"
        for item in record.evidence[:5]
        if item.excerpt
    ]
    evidence_text = "\n".join(evidence_lines) if evidence_lines else "- 暂无证据摘要"
    return (
        f"# {record.title}\n\n"
        "## 摘要\n"
        f"{record.summary}\n\n"
        "## 说明\n"
        f"{record.details}\n\n"
        "## 证据\n"
        f"{evidence_text}\n"
    )


def _target_path(config: MemoryConfig, vault_root: Path, note: VaultNote) -> Path:
    date_prefix = note.created[:10]
    filename = f"{date_prefix}_{note.note_type}_{_sanitize_filename(note.title)}.md"
    if note.route == "project" and note.project_name:
        subdir = PROJECT_SUBDIRS[note.note_type]
        return vault_root / config.obsidian.projects_dir / note.project_name / subdir / filename
    if note.route == "rules":
        return vault_root / config.obsidian.rules_dir / filename
    if note.note_type == "复盘":
        return vault_root / config.obsidian.reviews_dir / filename
    return vault_root / config.obsidian.inbox_dir / filename


def _resolve_path(desired_path: Path, current_path: Path | None) -> Path:
    if current_path is not None and current_path.exists():
        if current_path != desired_path:
            ensure_parent(desired_path)
            if desired_path.exists():
                desired_path = _next_available_path(desired_path)
            current_path.replace(desired_path)
        return desired_path
    ensure_parent(desired_path)
    if not desired_path.exists():
        return desired_path
    return _next_available_path(desired_path)


def _next_available_path(desired_path: Path) -> Path:
    stem = desired_path.stem
    suffix = desired_path.suffix
    counter = 2
    while True:
        candidate = desired_path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _write_note(path: Path, note: VaultNote) -> None:
    frontmatter = _build_frontmatter(note)
    content = f"---\n{frontmatter}---\n\n{note.body.strip()}\n"
    path.write_text(content, encoding="utf-8")


def _build_frontmatter(note: VaultNote) -> str:
    payload: dict[str, Any] = {
        "project": note.project_name or "",
        "project_key": note.project_key or "",
        "type": note.note_type,
        "status": note.status,
        "source": note.source,
        "tags": note.tags,
        "created": note.created,
        "updated": note.updated,
        "confidence": round(note.confidence, 3),
    }
    lines: list[str] = []
    for key in FRONTMATTER_ORDER:
        value = payload[key]
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
            continue
        lines.append(f'{key}: "{value}"')
    return "\n".join(lines) + "\n"


def _write_supporting_docs(config: MemoryConfig, vault_root: Path) -> None:
    inbox_readme = vault_root / config.obsidian.inbox_dir / "_待整理说明.md"
    if not inbox_readme.exists():
        inbox_readme.write_text(
            "# 收件箱说明\n\n"
            "- AI 自动整理后，项目或类型不够明确的内容会先进入这里。\n"
            "- 优先处理高价值条目：需求、计划、决策、复盘、规则。\n"
            "- 同主题优先合并，不要重复新建正式文档。\n",
            encoding="utf-8",
        )

    rules_doc = vault_root / config.obsidian.rules_dir / "_AI写入规则.md"
    if not rules_doc.exists():
        rules_doc.write_text(
            "# AI 写入规则\n\n"
            "- 默认不记录纯闲聊。\n"
            "- 默认不记录一次性临时提醒。\n"
            "- 默认不记录细碎执行痕迹。\n"
            "- 只记录未来自己或 AI 还会再次使用的内容。\n"
            "- 项目和类型都明确时再直写正式目录，否则先入收件箱。\n",
            encoding="utf-8",
        )

    review_doc = vault_root / config.obsidian.reviews_dir / "_月度整理流程.md"
    if not review_doc.exists():
        review_doc.write_text(
            "# 月度整理流程\n\n"
            "1. 清理收件箱中的陈旧条目。\n"
            "2. 将重复出现的经验上提到长期规则。\n"
            "3. 合并同项目同主题的重复文档。\n"
            "4. 将已完成项目的沉淀移动到归档或阶段复盘。\n",
            encoding="utf-8",
        )


def _write_weekly_summaries(config: MemoryConfig, vault_root: Path, store: MemoryStore, notes: list[VaultNote]) -> int:
    weekly_window_start = datetime.now(timezone.utc) - timedelta(days=7)
    week_label = _week_label(datetime.now(timezone.utc))
    records = _load_active_records(store)
    recent_notes = [note for note in notes if _note_datetime(note.updated) >= weekly_window_start]

    workstyle_path = vault_root / "工作模式和偏好.md"
    lessons_path = vault_root / "经验与教训.md"

    workstyle_content = _build_workstyle_summary(week_label, records, recent_notes)
    lessons_content = _build_lessons_summary(week_label, records, recent_notes)

    workstyle_path.write_text(workstyle_content, encoding="utf-8")
    lessons_path.write_text(lessons_content, encoding="utf-8")
    return 2


def _build_workstyle_summary(week_label: str, records: list[MemoryRecord], recent_notes: list[VaultNote]) -> str:
    now = datetime.now(timezone.utc)
    stable_cutoff = now - timedelta(days=45)
    preference_records = [
        record for record in records
        if record.memory_type in {"profile", "procedural"}
        and record.scope != "project"
        and any(tag in {"preference", "rule", "procedure"} for tag in record.tags)
        and _record_datetime(record) >= stable_cutoff
    ]
    stable_items = grounded_keep_best(
        title="工作模式和偏好的当前稳定模式",
        candidates=_ranked_record_summaries(preference_records, limit=10),
        max_items=8,
    )
    recent_preference_notes = [
        note for note in recent_notes
        if note.note_type in {"规则", "计划", "需求"}
    ]
    recent_items = grounded_keep_best(
        title=f"工作模式和偏好的本周新增观察（{week_label}）",
        candidates=_unique_bullets(note.title for note in recent_preference_notes)[:10],
        max_items=8,
    )
    pending_items = _unique_bullets(
        note.title for note in recent_notes
        if note.route == "inbox" and note.note_type in {"规则", "计划", "需求"}
    )[:6]
    parts = [
        "# 工作模式和偏好",
        "",
        "## 当前稳定模式",
        *_render_bullets(stable_items, "暂无稳定偏好总结。"),
        "",
        f"## 本周新增观察（{week_label}）",
        *_render_bullets(recent_items, "本周暂无新的工作模式观察。"),
        "",
        "## 待确认",
        *_render_bullets(pending_items, "当前没有待确认的偏好条目。"),
        "",
        "## 更新规则",
        "- 每周自动从高价值需求、计划、规则和活跃记忆中提炼。",
        "- 只保留未来会重复使用的习惯、默认做法和协作偏好。",
        "",
    ]
    return "\n".join(parts).strip() + "\n"


def _build_lessons_summary(week_label: str, records: list[MemoryRecord], recent_notes: list[VaultNote]) -> str:
    now = datetime.now(timezone.utc)
    reusable_cutoff = now - timedelta(days=60)
    lesson_records = [
        record for record in records
        if record.memory_type in {"procedural", "semantic"}
        and any(tag in {"watchout", "rule", "semantic"} for tag in record.tags)
        and _record_datetime(record) >= reusable_cutoff
        and _is_concrete_lesson_text(record.summary)
    ]
    reusable_patterns = grounded_keep_best(
        title="经验与教训的可复用模式",
        candidates=_ranked_record_summaries([record for record in lesson_records if record.scope != "project"], limit=12),
        max_items=10,
    )
    recent_lesson_notes = [
        note for note in recent_notes
        if note.note_type in {"复盘", "决策"}
        and _is_concrete_lesson_text(note.title)
        and (note.project_name is not None or note.confidence >= 0.82)
    ]
    success_items = grounded_keep_best(
        title=f"经验与教训的本周经验（{week_label}）",
        candidates=_unique_bullets(
            note.title for note in recent_lesson_notes
            if note.note_type == "决策"
        )[:10],
        max_items=8,
    )
    risk_items = grounded_keep_best(
        title=f"经验与教训的本周教训与风险（{week_label}）",
        candidates=_unique_bullets(
            note.title for note in recent_lesson_notes
            if note.note_type == "复盘"
        )[:10],
        max_items=8,
    )
    parts = [
        "# 经验与教训",
        "",
        f"## 本周经验（{week_label}）",
        *_render_bullets(success_items, "本周暂无新的经验总结。"),
        "",
        "## 本周教训与风险",
        *_render_bullets(risk_items, "本周暂无新的风险或教训。"),
        "",
        "## 可复用模式",
        *_render_bullets(reusable_patterns, "暂未沉淀出可复用模式。"),
        "",
        "## 更新规则",
        "- 只从明确复盘、明确决策、已验证规则中提炼。",
        "- 过滤过程话术、计划草稿、系统提示和没有结论的执行记录。",
        "",
    ]
    return "\n".join(parts).strip() + "\n"


def _load_active_records(store: MemoryStore) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for path in store.iter_memory_files():
        payload = json.loads(path.read_text(encoding="utf-8"))
        record = MemoryRecord.from_dict(payload)
        if record.status == "active":
            records.append(record)
    return records


def _record_datetime(record: MemoryRecord) -> datetime:
    return _note_datetime(record.last_seen_at or record.reviewed_at or record.created_at)


def _note_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


def _week_label(value: datetime) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _unique_bullets(items) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if not item:
            continue
        cleaned = trim_excerpt(str(item).strip(), 120)
        lowered = cleaned.lower()
        if not cleaned or lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(cleaned)
    return ordered[:12]


def _ranked_record_summaries(records: list[MemoryRecord], limit: int) -> list[str]:
    ranked = sorted(
        records,
        key=lambda record: (
            _record_datetime(record),
            record.confidence,
            record.stability,
            len(record.evidence),
        ),
        reverse=True,
    )
    return _unique_bullets(record.summary for record in ranked)[:limit]


def _is_concrete_lesson_text(text: str) -> bool:
    lowered = (text or "").lower().strip()
    if len(lowered) < 12:
        return False
    if any(marker in lowered for marker in LESSON_NOISE_MARKERS):
        return False
    if lowered.startswith("我") and not any(marker in lowered for marker in ("不要", "必须", "默认", "经验", "教训", "结论")):
        return False
    if not any(marker in lowered for marker in LESSON_SIGNAL_MARKERS):
        return False
    return True


def _render_bullets(items: list[str], empty_message: str) -> list[str]:
    if not items:
        return [f"- {empty_message}"]
    return [f"- {item}" for item in items]


def _import_new_obsidian_notes(config: MemoryConfig, store: MemoryStore) -> int:
    """检测 vault 中新增的笔记，导入为高置信度候选"""
    from ai_memory_hub.core.models import Evidence, utc_now

    vault_root = Path(config.obsidian.vault_root)
    if not vault_root.exists():
        return 0

    state_path = vault_root / ".ai-memory-hub-imported.json"
    imported_ids: set[str] = set()
    if state_path.exists():
        imported_ids = set(json.loads(state_path.read_text(encoding="utf-8")))

    imported = 0
    new_ids: list[str] = list(imported_ids)

    for md_file in vault_root.rglob("*.md"):
        if md_file.name.startswith("."):
            continue
        rel = md_file.relative_to(vault_root)
        note_id = f"obsidian:{rel}"

        if note_id in imported_ids:
            continue

        frontmatter, body = _parse_frontmatter(md_file)
        if not body or len(body) < 50:
            continue

        if not _is_high_value_note(body):
            continue

        title = (frontmatter.get("title") or md_file.stem)[:36]
        record_id = stable_id(
            "semantic", "global", "shared", None, title.lower(), body[:72].lower()
        )
        existing = store.load_memory(record_id)
        if existing:
            new_ids.append(note_id)
            continue

        evidence = [Evidence(
            source_tool="obsidian",
            source_path=str(md_file),
            session_id=None,
            timestamp=str(datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc)),
            excerpt=body[:220],
        )]
        record = MemoryRecord(
            id=record_id,
            title=title,
            memory_type="semantic",
            scope="global",
            tool="shared",
            project_key=None,
            summary=body[:72],
            details=body[:400],
            evidence=evidence,
            confidence=0.92,
            stability=0.95,
            sensitivity="low",
            tags=["obsidian-import", "high-confidence", "needs-review"],
            created_at=utc_now(),
            last_seen_at=utc_now(),
            reviewed_at=None,
            status="candidate",
            supersedes=None,
            managed_by="system",
            manual_override=False,
            last_accessed_at=None,
            expiration_days=90,
        )
        store.write_memory(record)
        new_ids.append(note_id)
        imported += 1

    if new_ids:
        state_path.write_text(json.dumps(new_ids, ensure_ascii=False, indent=2), encoding="utf-8")

    return imported


def _is_high_value_note(text: str) -> bool:
    """判断 Obsidian 笔记内容是否具有高价值"""
    if len(text.strip()) < 80:
        return False
    positive_markers = (
        "决策", "规则", "偏好", "记住", "应该", "不要", "目标",
        "决定", "取舍", "原则", "规范", "convention", "rule", "prefer",
        "decision", "pattern", "pattern:", "## 结论", "## 决策",
    )
    return any(marker in text for marker in positive_markers)


def _parse_frontmatter(path: Path) -> tuple[dict, str]:
    """从 Markdown 文件解析 YAML frontmatter 和正文"""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return {}, ""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter: dict[str, str] = {}
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    frontmatter[key.strip()] = val.strip()
            body = parts[2].strip()
            return frontmatter, body
    return {}, content.strip()


def _write_pending_list(config: MemoryConfig, vault_root: Path) -> int:
    inbox_root = vault_root / config.obsidian.inbox_dir
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.obsidian.inbox_pending_after_days)
    pending_paths: list[Path] = []
    for path in inbox_root.glob("*.md"):
        if path.name.startswith("_"):
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified <= cutoff:
            pending_paths.append(path)

    report_path = inbox_root / "_待处理清单.md"
    lines = ["# 待处理清单", ""]
    if pending_paths:
        lines.append(f"以下条目已在收件箱停留超过 {config.obsidian.inbox_pending_after_days} 天：")
        lines.append("")
        for path in sorted(pending_paths):
            lines.append(f"- [[{path.stem}]]")
    else:
        lines.append("当前没有超期未处理条目。")
    report_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return len(pending_paths)


def _state_path(store: MemoryStore) -> Path:
    return store.root / "state" / "obsidian_vault_index.json"


def _load_state(store: MemoryStore) -> dict[str, dict[str, str]]:
    path = _state_path(store)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(store: MemoryStore, state: dict[str, dict[str, str]]) -> None:
    path = _state_path(store)
    ensure_parent(path)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
