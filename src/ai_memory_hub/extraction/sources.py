"""
数据源解析模块。

解析 Codex、Claude、Cursor、Gemini 等工具的会话记录，
将其转换为统一的 RawEvent 格式供下游处理。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ai_memory_hub.core.config import MemoryConfig, ToolConfig
from ai_memory_hub.core.models import RawEvent
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.core.utils import project_key_from_path, stable_id


ParserFn = Callable[[str, Path, list[dict]], Iterable[RawEvent]]


@dataclass(slots=True)
class SourceSpec:
    path: Path
    parser: ParserFn


@dataclass(slots=True)
class ToolAdapter:
    tool_id: str
    discover: Callable[[ToolConfig], list[SourceSpec]]


def _normalize_message_text(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    normalized = text.strip()
    return normalized or None


def _iter_jsonl(path: Path, start_position: int = 0) -> tuple[list[dict], int]:
    items: list[dict] = []
    if not path.exists():
        return items, 0
    with path.open("r", encoding="utf-8-sig") as handle:
        if start_position:
            handle.seek(start_position)
        while True:
            line = handle.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            try:
                items.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return items, handle.tell()


def _collect_file_events(store: MemoryStore, source_spec: SourceSpec, tool_id: str) -> list[RawEvent]:
    source_path = source_spec.path
    if not source_path.exists():
        return []
    if source_path.suffix.lower() in {".txt", ".md"}:
        text = source_path.read_text(encoding="utf-8")
        payloads = [{"role": "user", "content": text, "session_id": source_path.stem}]
        return list(source_spec.parser(tool_id, source_path, payloads))
    file_size = source_path.stat().st_size
    previous_position, previous_size = store.get_cursor_state(str(source_path))
    start_position = previous_position if file_size >= previous_size else 0
    payloads, end_position = _iter_jsonl(source_path, start_position=start_position)
    events = list(source_spec.parser(tool_id, source_path, payloads))
    store.set_cursor_state(str(source_path), end_position, file_size, source_path.stat().st_mtime_ns)
    return events


def _parse_codex_history(tool_id: str, source_path: Path, payloads: list[dict]) -> Iterable[RawEvent]:
    for index, payload in enumerate(payloads):
        text = payload.get("text")
        if not text:
            continue
        yield RawEvent(
            id=stable_id(str(source_path), str(index), payload.get("session_id", ""), text),
            source_tool=tool_id,
            source_path=str(source_path),
            session_id=payload.get("session_id"),
            event_type="history_user_text",
            timestamp=str(payload.get("ts")) if payload.get("ts") else None,
            role="user",
            cwd=None,
            project_key=None,
            text=text,
            command=None,
            raw_json=json.dumps(payload, ensure_ascii=False),
        )


def _extract_codex_message_text(payload: dict) -> tuple[str | None, str | None]:
    role = payload.get("role")
    chunks = []
    for item in payload.get("content", []):
        text = item.get("text")
        if text:
            chunks.append(text)
    return role, _normalize_message_text("\n".join(chunks))


def _parse_codex_session(tool_id: str, source_path: Path, payloads: list[dict]) -> Iterable[RawEvent]:
    current_cwd = None
    current_session_id = None
    for index, payload in enumerate(payloads):
        line_type = payload.get("type")
        body = payload.get("payload", {})
        if line_type == "session_meta":
            current_cwd = body.get("cwd")
            current_session_id = body.get("id")
            continue
        if line_type == "response_item" and body.get("type") == "message":
            role, text = _extract_codex_message_text(body)
            if role not in {"user", "assistant", "developer", "system"} or not text:
                continue
            yield RawEvent(
                id=stable_id(str(source_path), str(index), role, text),
                source_tool=tool_id,
                source_path=str(source_path),
                session_id=current_session_id,
                event_type="message",
                timestamp=payload.get("timestamp"),
                role=role,
                cwd=current_cwd,
                project_key=project_key_from_path(current_cwd),
                text=text,
                command=None,
                raw_json=json.dumps(payload, ensure_ascii=False),
            )
        if line_type == "response_item" and body.get("type") in {"function_call", "tool_call"}:
            name = body.get("name") or body.get("tool_name")
            arguments = body.get("arguments") or body.get("args") or {}
            command = None
            cwd = current_cwd
            if isinstance(arguments, dict):
                command = arguments.get("command")
                cwd = arguments.get("workdir") or cwd
            yield RawEvent(
                id=stable_id(str(source_path), str(index), name or "", command or ""),
                source_tool=tool_id,
                source_path=str(source_path),
                session_id=current_session_id,
                event_type="tool_use",
                timestamp=payload.get("timestamp"),
                role="assistant",
                cwd=cwd,
                project_key=project_key_from_path(cwd),
                text=name,
                command=command,
                raw_json=json.dumps(payload, ensure_ascii=False),
            )


def _parse_claude_transcript(tool_id: str, source_path: Path, payloads: list[dict]) -> Iterable[RawEvent]:
    for index, payload in enumerate(payloads):
        line_type = payload.get("type")
        if line_type == "user":
            text = payload.get("content")
            if not text:
                continue
            yield RawEvent(
                id=stable_id(str(source_path), str(index), text),
                source_tool=tool_id,
                source_path=str(source_path),
                session_id=source_path.stem,
                event_type="message",
                timestamp=payload.get("timestamp"),
                role="user",
                cwd=None,
                project_key=None,
                text=text,
                command=None,
                raw_json=json.dumps(payload, ensure_ascii=False),
            )
        elif line_type == "tool_use":
            tool_input = payload.get("tool_input", {})
            cwd = tool_input.get("workdir")
            command = tool_input.get("command")
            yield RawEvent(
                id=stable_id(str(source_path), str(index), payload.get("tool_name", ""), command or ""),
                source_tool=tool_id,
                source_path=str(source_path),
                session_id=source_path.stem,
                event_type="tool_use",
                timestamp=payload.get("timestamp"),
                role="assistant",
                cwd=cwd,
                project_key=project_key_from_path(cwd),
                text=payload.get("tool_name"),
                command=command,
                raw_json=json.dumps(payload, ensure_ascii=False),
            )


def _extract_cursor_message_text(payload: dict) -> tuple[str | None, str | None]:
    role = payload.get("role")
    message = payload.get("message", {})
    content = message.get("content", [])
    if not isinstance(content, list):
        return role, None
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = _normalize_message_text(item.get("text"))
        if text:
            chunks.append(text)
    return role, _normalize_message_text("\n".join(chunks))


def _is_cursor_internal_thought(role: str | None, text: str | None) -> bool:
    if role != "assistant" or not text:
        return False
    lowered = text.lstrip().lower()
    return lowered.startswith("<think>") or lowered.startswith("```think")


def _parse_cursor_transcript(tool_id: str, source_path: Path, payloads: list[dict]) -> Iterable[RawEvent]:
    session_id = source_path.stem
    project_root = source_path.parent.parent.parent
    cwd = str(project_root)
    project_key = project_key_from_path(cwd)
    for index, payload in enumerate(payloads):
        if not isinstance(payload, dict):
            continue
        role, text = _extract_cursor_message_text(payload)
        if role not in {"user", "assistant"} or not text or _is_cursor_internal_thought(role, text):
            continue
        yield RawEvent(
            id=stable_id(str(source_path), str(index), role, text),
            source_tool=tool_id,
            source_path=str(source_path),
            session_id=session_id,
            event_type="message",
            timestamp=str(payload.get("timestamp")) if payload.get("timestamp") else None,
            role=role,
            cwd=cwd,
            project_key=project_key,
            text=text,
            command=None,
            raw_json=json.dumps(payload, ensure_ascii=False),
        )


def _generic_message_payload(tool_id: str, source_path: Path, payload: dict, index: int) -> RawEvent | None:
    role = payload.get("role") or payload.get("type") or "user"
    text = payload.get("text") or payload.get("content") or payload.get("message")
    if isinstance(text, list):
        text = "\n".join(str(item) for item in text if item)
    text = _normalize_message_text(text)
    if not text:
        return None
    cwd = payload.get("cwd") or payload.get("workdir")
    command = payload.get("command")
    session_id = payload.get("session_id") or payload.get("conversation_id") or source_path.stem
    event_type = "tool_use" if command else "message"
    return RawEvent(
        id=stable_id(str(source_path), str(index), role, text, command or ""),
        source_tool=tool_id,
        source_path=str(source_path),
        session_id=session_id,
        event_type=event_type,
        timestamp=str(payload.get("timestamp") or payload.get("ts")) if payload.get("timestamp") or payload.get("ts") else None,
        role=role if role in {"user", "assistant", "developer", "system"} else "user",
        cwd=cwd,
        project_key=project_key_from_path(cwd),
        text=text,
        command=command,
        raw_json=json.dumps(payload, ensure_ascii=False),
    )


def _parse_generic_jsonl(tool_id: str, source_path: Path, payloads: list[dict]) -> Iterable[RawEvent]:
    for index, payload in enumerate(payloads):
        event = _generic_message_payload(tool_id, source_path, payload, index)
        if event is not None:
            yield event


def _discover_codex(tool: ToolConfig) -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    sessions_root = Path(tool.source_paths.get("sessions", ""))
    if sessions_root.exists():
        specs.extend(SourceSpec(path=session_file, parser=_parse_codex_session) for session_file in sorted(sessions_root.rglob("*.jsonl")))
    history_path = Path(tool.source_paths.get("history", ""))
    if history_path.exists():
        specs.append(SourceSpec(path=history_path, parser=_parse_codex_history))
    return specs


def _discover_claude(tool: ToolConfig) -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    transcripts_root = Path(tool.source_paths.get("transcripts", ""))
    if transcripts_root.exists():
        specs.extend(SourceSpec(path=transcript_file, parser=_parse_claude_transcript) for transcript_file in sorted(transcripts_root.glob("*.jsonl")))
    history_path = Path(tool.source_paths.get("history", ""))
    if history_path.exists():
        specs.append(SourceSpec(path=history_path, parser=_parse_claude_transcript))
    return specs


def _discover_cursor(tool: ToolConfig) -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    seen: set[Path] = set()
    for value in tool.source_paths.values():
        if not value:
            continue
        path = Path(value)
        if not path.exists():
            continue
        candidates: list[Path] = []
        if path.is_file() and path.suffix.lower() == ".jsonl":
            candidates = [path]
        elif path.is_dir():
            if path.name == "agent-transcripts":
                candidates = sorted(path.rglob("*.jsonl"))
            else:
                direct_dir = path / "agent-transcripts"
                if direct_dir.exists():
                    candidates = sorted(direct_dir.rglob("*.jsonl"))
                else:
                    candidates = sorted(
                        candidate
                        for candidate in path.rglob("*.jsonl")
                        if "agent-transcripts" in candidate.parts
                    )
        for candidate in candidates:
            if candidate not in seen:
                specs.append(SourceSpec(path=candidate, parser=_parse_cursor_transcript))
                seen.add(candidate)
    return specs


def _discover_generic_jsonl(tool: ToolConfig) -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    seen: set[Path] = set()
    for value in tool.source_paths.values():
        path = Path(value)
        if not path.exists():
            continue
        if path.is_file() and path.suffix.lower() in {".jsonl", ".txt", ".md"} and path not in seen:
            specs.append(SourceSpec(path=path, parser=_parse_generic_jsonl))
            seen.add(path)
            continue
        if path.is_dir():
            candidates = list(path.rglob("*.jsonl")) + list(path.rglob("*.txt")) + list(path.rglob("*.md"))
            for candidate in sorted(candidates):
                if candidate not in seen:
                    specs.append(SourceSpec(path=candidate, parser=_parse_generic_jsonl))
                    seen.add(candidate)
    return specs


def _parse_manual_note(source_path: Path) -> Iterable[RawEvent]:
    text = source_path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    return [
        RawEvent(
            id=stable_id(str(source_path), text[:120]),
            source_tool="manual",
            source_path=str(source_path),
            session_id=None,
            event_type="manual_note",
            timestamp=None,
            role="user",
            cwd=None,
            project_key=None,
            text=text,
            command=None,
            raw_json=json.dumps({"path": str(source_path), "text": text}, ensure_ascii=False),
        )
    ]


def _tool_adapters() -> dict[str, ToolAdapter]:
    return {
        "codex": ToolAdapter(tool_id="codex", discover=_discover_codex),
        "claude": ToolAdapter(tool_id="claude", discover=_discover_claude),
        "gemini": ToolAdapter(tool_id="gemini", discover=_discover_generic_jsonl),
        "opencode": ToolAdapter(tool_id="opencode", discover=_discover_generic_jsonl),
        "cursor": ToolAdapter(tool_id="cursor", discover=_discover_cursor),
    }


def collect_sources(config: MemoryConfig, store: MemoryStore) -> dict[str, int]:
    counts: dict[str, int] = {"manual": 0}
    adapters = _tool_adapters()
    for tool in config.enabled_tools:
        adapter = adapters.get(tool.id, ToolAdapter(tool_id=tool.id, discover=_discover_generic_jsonl))
        tool_total = 0
        for source_spec in adapter.discover(tool):
            tool_total += store.insert_raw_events(_collect_file_events(store, source_spec, tool.id))
        counts[tool.id] = tool_total

    for root_value in [config.sources.manual_notes, config.sources.manual_rules]:
        root = Path(root_value)
        if not root.exists():
            continue
        for file_path in sorted(root.rglob("*")):
            if file_path.is_file() and file_path.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml"}:
                counts["manual"] += store.insert_raw_events(list(_parse_manual_note(file_path)))
    return counts
