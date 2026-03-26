"""
渲染服务模块。

将记忆渲染为 Markdown 格式输出，支持：
- 按类型输出列表
- Obsidian Markdown 格式
- 带置信度星标
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_memory_hub.core.config import MemoryConfig, ToolConfig, ToolRenderTarget
from ai_memory_hub.core.models import MemoryRecord
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.core.utils import ensure_parent


def _load_records(store: MemoryStore, exclude_contradicted: bool = True) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for path in store.iter_memory_files():
        try:
            record = MemoryRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if exclude_contradicted and record.status == "contradicted":
                continue
            records.append(record)
        except Exception:
            continue
    return records


def _bullet_lines(records: list[MemoryRecord], max_items: int) -> list[str]:
    lines: list[str] = []
    for record in sorted(records, key=lambda item: (-item.confidence, -item.stability, item.title))[:max_items]:
        stars = _confidence_stars(record.confidence)
        last_source = _last_source_tool(record)
        last_days = _last_seen_days(record)
        if record.status == "contradicted":
            lines.append(
                f"- ~~[{stars}] ~~{record.title}~~~~  (已矛盾，{last_source} · {last_days})"
            )
        else:
            lines.append(
                f"- **[{stars}] {record.title}**  (conf:{record.confidence:.2f}, stab:{record.stability:.2f})\n"
                f"  来源：{len(record.evidence)}个来源 | 最新：{last_source} · {last_days}"
            )
    return lines or ["- No active memories yet."]


def _confidence_stars(confidence: float) -> str:
    if confidence >= 0.85:
        return "★★★"
    elif confidence >= 0.75:
        return "★★☆"
    elif confidence >= 0.65:
        return "★☆☆"
    return "☆☆☆"


def _last_source_tool(record: MemoryRecord) -> str:
    if record.evidence:
        return record.evidence[-1].source_tool.title()
    return "Unknown"


def _last_seen_days(record: MemoryRecord) -> str:
    from datetime import datetime, timezone
    ts = record.last_seen_at or record.reviewed_at or record.created_at
    ts = str(ts).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts)
        days = (datetime.now(timezone.utc) - dt).days
        if days == 0:
            return "今天"
        elif days == 1:
            return "1天前"
        return f"{days}天前"
    except Exception:
        return "未知"


def _render_markdown(path: Path, title: str, sections: list[tuple[str, list[str]]]) -> None:
    ensure_parent(path)
    parts = [f"# {title}", ""]
    for heading, lines in sections:
        parts.append(f"## {heading}")
        parts.extend(lines)
        parts.append("")
    path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")


def _tool_records(records: list[MemoryRecord], tool: ToolConfig) -> list[MemoryRecord]:
    return [item for item in records if item.tool in {"shared", tool.id} and item.scope != "project"]


def _choose_current_project(project_groups: dict[str, list[MemoryRecord]]) -> tuple[str, list[MemoryRecord]] | None:
    if not project_groups:
        return None
    ordered = sorted(
        project_groups.items(),
        key=lambda item: max(record.last_seen_at or record.created_at or "" for record in item[1]),
        reverse=True,
    )
    return ordered[0]


def _project_sections(project_records: list[MemoryRecord], max_items: int) -> list[tuple[str, list[str]]]:
    architecture = [item for item in project_records if item.memory_type == "semantic" or "architecture" in item.tags]
    commands = [item for item in project_records if "commands" in item.tags or "maven" in item.tags]
    constraints = [item for item in project_records if "constraint" in item.tags or "coding-style" in item.tags]
    watch_outs = [item for item in project_records if "watchout" in item.tags or "local-sdk" in item.tags]
    return [
        ("Architecture", _bullet_lines(architecture, max_items)),
        ("Commands", _bullet_lines(commands, max_items)),
        ("Constraints", _bullet_lines(constraints, max_items)),
        ("Watch Outs", _bullet_lines(watch_outs, max_items)),
    ]


def render_outputs(config: MemoryConfig, store: MemoryStore) -> dict[str, int]:
    records = [item for item in _load_records(store) if item.status == "active"]
    rendered_root = config.data_home_path / "rendered"
    max_items = config.scan.max_render_items

    global_profile = [item for item in records if item.memory_type == "profile" and item.scope == "global"]
    global_procedures = [item for item in records if item.memory_type == "procedural" and item.scope in {"global", "tool"}]
    watchouts = [item for item in records if "watchout" in item.tags]

    project_groups: dict[str, list[MemoryRecord]] = {}
    for item in records:
        if item.scope == "project" and item.project_key:
            project_groups.setdefault(item.project_key, []).append(item)
    current_project = _choose_current_project(project_groups)
    current_project_rules = current_project[1] if current_project else []

    _render_markdown(rendered_root / "personal-profile.md", "Personal Profile", [("Stable Preferences", _bullet_lines(global_profile, max_items))])
    _render_markdown(rendered_root / "working-style.md", "Working Style", [("Procedures", _bullet_lines(global_procedures, max_items))])
    _render_markdown(
        rendered_root / "memory-brief.md",
        "Memory Brief",
        [
            ("Must Follow", _bullet_lines([item for item in records if item.memory_type == "procedural" and item.scope != "project"], 10)),
            ("Preferences", _bullet_lines([item for item in records if item.memory_type == "profile"], 10)),
            ("Known Patterns", _bullet_lines([item for item in records if item.memory_type == "semantic" and item.scope != "project"], 10)),
            ("Current Project Rules", _bullet_lines(current_project_rules, 10)),
        ],
    )

    rendered_files = 3
    for tool in config.enabled_tools:
        tool_records = _tool_records(records, tool)
        _render_markdown(
            rendered_root / f"{tool.id}-memory.md",
            f"{tool.label} Memory",
            [
                ("Must Follow", _bullet_lines([item for item in tool_records if item.memory_type == "procedural"], max_items)),
                ("Preferences", _bullet_lines([item for item in tool_records if item.memory_type == "profile"], max_items)),
                ("Watch Outs", _bullet_lines([item for item in watchouts if item.tool in {"shared", tool.id}], max_items)),
            ],
        )
        rendered_files += 1

    project_count = 0
    for project_key, project_records in project_groups.items():
        _render_markdown(
            rendered_root / "projects" / project_key / "project-memory.md",
            f"Project Memory: {project_key}",
            _project_sections(project_records, max_items),
        )
        project_count += 1

    for tool in config.enabled_tools:
        for target in tool.render_targets:
            _sync_tool_render(rendered_root, tool, target)

    return {"rendered_files": rendered_files + project_count, "project_files": project_count}


def _sync_tool_render(rendered_root: Path, tool: ToolConfig, target: ToolRenderTarget) -> None:
    common_files = ["personal-profile.md", "working-style.md", "memory-brief.md", f"{tool.id}-memory.md"]
    if target.kind == "directory_copy":
        destination = Path(target.path)
        destination.mkdir(parents=True, exist_ok=True)
        for filename in common_files:
            shutil.copy2(rendered_root / filename, destination / filename)
        return
    if target.kind == "claude_skill":
        _sync_claude_skill(rendered_root, Path(target.path), common_files, tool)


def _sync_claude_skill(rendered_root: Path, skill_root: Path, filenames: list[str], tool: ToolConfig) -> None:
    references_root = skill_root / "references"
    references_root.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        shutil.copy2(rendered_root / filename, references_root / filename)
    skill_md = skill_root / "SKILL.md"
    if not skill_md.exists():
        skill_md.write_text(
            f"""---
name: personal-memory
description: Use this skill when you need the user's long-term preferences, working style, watch-outs, or current project memory.
---

# Personal Memory

Read the reference files in `references/` before answering when the task would benefit from user-specific habits, procedures, or preferences.

Priority:
1. `references/memory-brief.md`
2. `references/{tool.id}-memory.md`
3. `references/personal-profile.md`
4. `references/working-style.md`
""",
            encoding="utf-8",
        )
