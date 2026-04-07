from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from ai_memory_hub.core.config import BootstrapProject, MemoryConfig
from ai_memory_hub.core.models import MemoryRecord, utc_now
from ai_memory_hub.core.utils import (
    ensure_parent,
    normalize_project_reference,
    project_key_from_path,
    slugify,
    trim_excerpt,
)
from ai_memory_hub.storage.db import MemoryStore


GENERIC_TAGS = {
    "shared",
    "watchout",
    "preference",
    "procedure",
    "rule",
    "fact",
    "profile",
    "procedural",
    "semantic",
    "episodic",
}
TYPE_LABELS = {
    "profile": "Profiles",
    "procedural": "Procedures",
    "semantic": "Patterns",
    "episodic": "Episodes",
}
NEGATIVE_MARKERS = ("never", "do not", "don't", "avoid", "forbid", "ban", "disable", "不要", "禁止", "避免")
POSITIVE_MARKERS = ("always", "must", "prefer", "enable", "adopt", "should", "应该", "优先", "必须")


@dataclass(slots=True)
class ProjectDisplayNameResolverResult:
    project_key: str
    display_name: str
    source: str
    low_quality: bool = False


@dataclass(slots=True)
class WikiLintIssue:
    issue_type: str
    severity: str
    message: str
    page_key: str | None = None
    memory_ids: list[str] = field(default_factory=list)
    sample_titles: list[str] = field(default_factory=list)
    suggestion: str = ""


@dataclass(slots=True)
class WikiLintReport:
    generated_at: str
    issue_counts: dict[str, int]
    issues: list[WikiLintIssue]
    low_quality_project_names: list[ProjectDisplayNameResolverResult]

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "issue_counts": dict(self.issue_counts),
            "issues": [asdict(item) for item in self.issues],
            "low_quality_project_names": [asdict(item) for item in self.low_quality_project_names],
        }

    def summary(self) -> dict[str, object]:
        return {
            "issue_counts": dict(self.issue_counts),
            "sample_issues": [
                {
                    "issue_type": item.issue_type,
                    "severity": item.severity,
                    "message": item.message,
                    "memory_ids": item.memory_ids[:3],
                    "sample_titles": item.sample_titles[:3],
                }
                for item in self.issues[:10]
            ],
            "low_quality_project_names": [item.project_key for item in self.low_quality_project_names[:10]],
        }


@dataclass(slots=True)
class WikiBuildState:
    records: dict[str, dict[str, object]] = field(default_factory=dict)
    page_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {"records": self.records, "page_hashes": self.page_hashes}

    @staticmethod
    def from_path(path: Path) -> "WikiBuildState":
        if not path.exists():
            return WikiBuildState()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return WikiBuildState()
        return WikiBuildState(
            records=dict(payload.get("records", {})),
            page_hashes=dict(payload.get("page_hashes", {})),
        )


@dataclass(slots=True)
class ActiveRecord:
    record: MemoryRecord
    source_path: Path
    source_mtime_ns: int


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _load_active_records(store: MemoryStore) -> list[ActiveRecord]:
    records: list[ActiveRecord] = []
    for path in store.iter_memory_files():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = MemoryRecord.from_dict(payload)
        except Exception:
            continue
        if record.status != "active":
            continue
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        records.append(ActiveRecord(record=record, source_path=path, source_mtime_ns=mtime_ns))
    return records


def _wiki_link(target: str, label: str | None = None) -> str:
    if label and label != target:
        return f"[[{target}|{label}]]"
    return f"[[{target}]]"


def _clean_project_label(value: str) -> str:
    value = re.sub(r"-[a-f0-9]{16}$", "", value)
    value = value.replace("_", " ").replace("-", " ").strip()
    value = re.sub(r"\s+", " ", value)
    if not value:
        return ""
    return " ".join(part.upper() if part.isupper() else part.capitalize() for part in value.split())


def _low_quality_project_name(project_key: str, display_name: str) -> bool:
    lowered = display_name.lower().strip()
    return (
        lowered == project_key.lower().strip()
        or bool(re.search(r"[a-f0-9]{12,}", lowered))
        or lowered in {"path", "project", "repo", "workspace", "unknown"}
    )


def _project_alias_from_records(project_key: str, items: list[MemoryRecord]) -> str | None:
    patterns = (
        re.compile(r"\bproject[:\s]+([A-Za-z0-9 _-]{3,40})", re.IGNORECASE),
        re.compile(r"\brepo[:\s]+([A-Za-z0-9 _-]{3,40})", re.IGNORECASE),
    )
    for item in sorted(items, key=lambda current: (-current.confidence, current.title)):
        text_candidates = [item.title, item.summary, item.details]
        for text in text_candidates:
            for pattern in patterns:
                match = pattern.search(text)
                if not match:
                    continue
                candidate = _clean_project_label(match.group(1))
                if candidate and len(candidate) >= 3:
                    return candidate
    cleaned = _clean_project_label(project_key)
    return cleaned or None


def _resolve_project_names(config: MemoryConfig, records: list[MemoryRecord]) -> dict[str, ProjectDisplayNameResolverResult]:
    grouped: dict[str, list[MemoryRecord]] = {}
    for record in records:
        if record.project_key:
            grouped.setdefault(record.project_key, []).append(record)

    bootstrap_by_key: dict[str, BootstrapProject] = {}
    for project in config.bootstrap_projects:
        if project.id:
            bootstrap_by_key[project.id] = project
        if project.path:
            normalized = normalize_project_reference(project.path)
            if normalized:
                bootstrap_by_key[normalized] = project
            derived = project_key_from_path(project.path)
            if derived:
                bootstrap_by_key[derived] = project

    resolved: dict[str, ProjectDisplayNameResolverResult] = {}
    for project_key, items in grouped.items():
        bootstrap = bootstrap_by_key.get(project_key)
        if bootstrap and bootstrap.name:
            display_name = bootstrap.name.strip()
            source = "bootstrap_projects"
        else:
            inferred = _project_alias_from_records(project_key, items)
            if inferred and inferred.lower() != project_key.lower():
                display_name = inferred
                source = "memory_or_key_inference"
            else:
                display_name = project_key
                source = "project_key_fallback"
        resolved[project_key] = ProjectDisplayNameResolverResult(
            project_key=project_key,
            display_name=display_name,
            source=source,
            low_quality=_low_quality_project_name(project_key, display_name),
        )
    return resolved


def _topic_tags(record: MemoryRecord) -> list[str]:
    return [tag for tag in record.tags if tag not in GENERIC_TAGS]


def _eligible_topics(records: list[MemoryRecord]) -> dict[str, list[MemoryRecord]]:
    grouped: dict[str, list[MemoryRecord]] = {}
    for record in records:
        for tag in _topic_tags(record):
            grouped.setdefault(tag, []).append(record)
    return {
        tag: items
        for tag, items in grouped.items()
        if len({item.id for item in items}) >= 2
    }


def _record_page_keys(record: MemoryRecord, eligible_topics: set[str]) -> list[str]:
    page_keys = [f"types/{record.memory_type}"]
    if record.project_key:
        page_keys.append(f"projects/{record.project_key}/index")
    for tag in _topic_tags(record):
        if tag in eligible_topics:
            page_keys.append(f"topics/{slugify(tag)}")
    page_keys.extend(["index", "health", "health.json"])
    return page_keys


def _record_line(record: MemoryRecord, project_names: dict[str, ProjectDisplayNameResolverResult]) -> str:
    bits = [f"- **{record.title}**", f"type: `{record.memory_type}`", f"conf: `{record.confidence:.2f}`"]
    if record.project_key:
        project_name = project_names.get(record.project_key)
        label = project_name.display_name if project_name else record.project_key
        bits.append(f"project: {_wiki_link(f'projects/{record.project_key}/index', label)}")
    topic_links = [_wiki_link(f"topics/{slugify(tag)}", tag) for tag in _topic_tags(record)]
    if topic_links:
        bits.append("topics: " + ", ".join(topic_links[:4]))
    return "  ".join(bits) + f"\n  {trim_excerpt(record.summary, 180)}"


def _render_markdown(title: str, lines: list[str]) -> str:
    content = [f"# {title}", ""]
    content.extend(lines)
    return "\n".join(content).rstrip() + "\n"


def _render_type_pages(records: list[MemoryRecord], project_names: dict[str, ProjectDisplayNameResolverResult]) -> dict[str, str]:
    grouped: dict[str, list[MemoryRecord]] = {}
    for record in records:
        grouped.setdefault(record.memory_type, []).append(record)

    rendered: dict[str, str] = {}
    for memory_type, items in grouped.items():
        items = sorted(items, key=lambda item: (-item.confidence, -item.stability, item.title))
        lines = [
            f"Generated from `{len(items)}` active memories.",
            "",
            "## Related",
            f"- {_wiki_link('index', 'Wiki Home')}",
            "",
            "## Entries",
        ]
        lines.extend(_record_line(item, project_names) for item in items)
        rendered[f"types/{memory_type}"] = _render_markdown(TYPE_LABELS.get(memory_type, memory_type.title()), lines)
    return rendered


def _project_issue_messages(issues: list[WikiLintIssue], project_key: str) -> list[str]:
    related = [item for item in issues if item.page_key == f"projects/{project_key}/index" or project_key in item.memory_ids]
    return [f"- `{item.issue_type}` {item.message}" for item in related[:6]]


def _render_project_pages(
    records: list[MemoryRecord],
    project_names: dict[str, ProjectDisplayNameResolverResult],
    issues: list[WikiLintIssue],
) -> dict[str, str]:
    grouped: dict[str, list[MemoryRecord]] = {}
    for record in records:
        if record.project_key:
            grouped.setdefault(record.project_key, []).append(record)

    rendered: dict[str, str] = {}
    for project_key, items in grouped.items():
        items = sorted(items, key=lambda item: (-item.confidence, -item.stability, item.title))
        meta = project_names.get(project_key)
        display_name = meta.display_name if meta else project_key
        related_topics = sorted({tag for item in items for tag in _topic_tags(item)})
        lines = [
            "## Summary",
            f"- Display name: `{display_name}`",
            f"- Project key: `{project_key}`",
            f"- Active memories: `{len(items)}`",
            f"- Name source: `{meta.source if meta else 'unknown'}`",
            "",
            "## Related Topics",
        ]
        if related_topics:
            lines.extend(f"- {_wiki_link(f'topics/{slugify(tag)}', tag)}" for tag in related_topics[:12])
        else:
            lines.append("- No topic pages yet.")
        warnings = _project_issue_messages(issues, project_key)
        if warnings:
            lines.extend(["", "## Governance Warnings"])
            lines.extend(warnings)
        lines.extend(["", "## Entries"])
        lines.extend(_record_line(item, project_names) for item in items)
        rendered[f"projects/{project_key}/index"] = _render_markdown(f"Project {display_name}", lines)
    return rendered


def _render_topic_pages(
    topic_records: dict[str, list[MemoryRecord]],
    project_names: dict[str, ProjectDisplayNameResolverResult],
) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for tag, items in sorted(topic_records.items()):
        items = sorted(items, key=lambda item: (-item.confidence, -item.stability, item.title))
        unique_projects = sorted({item.project_key for item in items if item.project_key})
        lines = [
            "## Summary",
            f"- Active memories: `{len(items)}`",
            f"- Unique projects: `{len(unique_projects)}`",
            "",
            "## Related",
            f"- {_wiki_link('index', 'Wiki Home')}",
        ]
        if unique_projects:
            lines.append("- Projects: " + ", ".join(
                _wiki_link(
                    f"projects/{key}/index",
                    project_names.get(key).display_name if project_names.get(key) else key,
                )
                for key in unique_projects[:8]
            ))
        lines.extend(["", "## Entries"])
        lines.extend(_record_line(item, project_names) for item in items)
        rendered[f"topics/{slugify(tag)}"] = _render_markdown(f"Topic {tag}", lines)
    return rendered


def _render_index(
    records: list[MemoryRecord],
    project_names: dict[str, ProjectDisplayNameResolverResult],
    topic_records: dict[str, list[MemoryRecord]],
) -> str:
    lines = [
        "## Overview",
        f"- Active memories: `{len(records)}`",
        f"- {_wiki_link('types/profile', 'Profiles')}",
        f"- {_wiki_link('types/procedural', 'Procedures')}",
        f"- {_wiki_link('types/semantic', 'Patterns')}",
        f"- {_wiki_link('types/episodic', 'Episodes')}",
        f"- {_wiki_link('health', 'Wiki Health')}",
        "",
        "## Projects",
    ]
    if project_names:
        for project_key, meta in sorted(project_names.items(), key=lambda item: item[1].display_name.lower()):
            lines.append(f"- {_wiki_link(f'projects/{project_key}/index', meta.display_name)}")
    else:
        lines.append("- No project pages yet.")
    lines.extend(["", "## Topics"])
    if topic_records:
        for tag in sorted(topic_records)[:24]:
            lines.append(f"- {_wiki_link(f'topics/{slugify(tag)}', tag)}")
    else:
        lines.append("- No topic pages yet.")
    return _render_markdown("Memory Wiki", lines)


def _token_set(text: str) -> set[str]:
    return {token for token in re.findall(r"[A-Za-z0-9_]+", text.lower()) if len(token) > 2}


def _polarity(text: str) -> int:
    lowered = text.lower()
    negative = any(marker in lowered for marker in NEGATIVE_MARKERS)
    positive = any(marker in lowered for marker in POSITIVE_MARKERS)
    if positive and not negative:
        return 1
    if negative and not positive:
        return -1
    return 0


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _stale_pages(root: Path, desired_page_keys: set[str]) -> list[Path]:
    stale: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == ".state.json":
            continue
        if path.suffix not in {".md", ".json"}:
            continue
        rel = path.relative_to(root).as_posix()
        page_key = rel[:-3] if rel.endswith(".md") else rel
        if page_key not in desired_page_keys:
            stale.append(path)
    return stale


def _build_lint_report(
    records: list[MemoryRecord],
    topic_records: dict[str, list[MemoryRecord]],
    project_names: dict[str, ProjectDisplayNameResolverResult],
    desired_page_keys: set[str],
    wiki_root: Path,
) -> WikiLintReport:
    issues: list[WikiLintIssue] = []

    for record in records:
        evidence_strength = sum(len((item.excerpt or "").strip()) for item in record.evidence)
        if not record.evidence or evidence_strength < 16:
            issues.append(WikiLintIssue(
                issue_type="weak_evidence",
                severity="medium",
                message=f"Memory `{record.id}` has weak or missing evidence.",
                memory_ids=[record.id],
                sample_titles=[record.title],
                suggestion="Review the memory and attach stronger excerpts before treating it as durable knowledge.",
            ))
        if record.scope == "project" and not record.project_key:
            issues.append(WikiLintIssue(
                issue_type="missing_project_key",
                severity="high",
                message=f"Project-scoped memory `{record.id}` is missing project_key.",
                memory_ids=[record.id],
                sample_titles=[record.title],
                suggestion="Backfill project_key so the memory can be routed into a project page.",
            ))

    for tag, items in topic_records.items():
        if len(items) >= 8:
            avg_summary = sum(len(item.summary.strip()) for item in items) / max(1, len(items))
            if avg_summary < 80:
                issues.append(WikiLintIssue(
                    issue_type="overbroad_topic",
                    severity="medium",
                    message=f"Topic `{tag}` is broad and may need splitting.",
                    page_key=f"topics/{slugify(tag)}",
                    memory_ids=[item.id for item in items[:6]],
                    sample_titles=[item.title for item in items[:6]],
                    suggestion="Split this topic into narrower tags or add more specific labels.",
                ))

    for tag, items in topic_records.items():
        for idx, first in enumerate(items):
            for second in items[idx + 1:]:
                if first.project_key != second.project_key:
                    continue
                token_overlap = len(_token_set(first.title) & _token_set(second.title))
                if token_overlap < 1:
                    continue
                if _polarity(first.summary + " " + first.details) * _polarity(second.summary + " " + second.details) != -1:
                    continue
                issues.append(WikiLintIssue(
                    issue_type="conflicting_topic",
                    severity="high",
                    message=f"Topic `{tag}` has conflicting guidance inside project `{first.project_key}`.",
                    page_key=f"topics/{slugify(tag)}",
                    memory_ids=[first.id, second.id],
                    sample_titles=[first.title, second.title],
                    suggestion="Review these memories and keep only the current rule or mark one as contradicted.",
                ))

    for idx, first in enumerate(records):
        for second in records[idx + 1:]:
            if first.project_key != second.project_key or first.memory_type != second.memory_type:
                continue
            title_similarity = _similarity(first.title, second.title)
            summary_similarity = _similarity(first.summary, second.summary)
            if title_similarity >= 0.83 or (title_similarity >= 0.72 and summary_similarity >= 0.72):
                issues.append(WikiLintIssue(
                    issue_type="duplicate_concept",
                    severity="low",
                    message="Two memories look like duplicate expressions of the same concept.",
                    page_key=f"projects/{first.project_key}/index" if first.project_key else None,
                    memory_ids=[first.id, second.id],
                    sample_titles=[first.title, second.title],
                    suggestion="Consider merging these memories or keeping the higher quality one.",
                ))

    backlinks: dict[str, int] = {page_key: 0 for page_key in desired_page_keys}
    for page_key in list(desired_page_keys):
        if page_key.startswith("projects/") or page_key.startswith("topics/") or page_key.startswith("types/"):
            backlinks[page_key] = 0
    backlinks["index"] = 1
    backlinks["health"] = 1
    backlinks["health.json"] = 1
    for page_key in desired_page_keys:
        if page_key.startswith("projects/") or page_key.startswith("topics/") or page_key.startswith("types/"):
            backlinks[page_key] = 1
    for path in _stale_pages(wiki_root, desired_page_keys):
        rel = path.relative_to(wiki_root).as_posix()
        issues.append(WikiLintIssue(
            issue_type="orphan_page",
            severity="medium",
            message=f"Stale wiki page `{rel}` is no longer linked from the generated graph.",
            page_key=rel[:-3] if rel.endswith(".md") else rel,
            suggestion="Delete the stale page or rebuild the wiki to clean it up.",
        ))

    low_quality_names = [item for item in project_names.values() if item.low_quality]
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.issue_type] = counts.get(issue.issue_type, 0) + 1

    return WikiLintReport(
        generated_at=utc_now(),
        issue_counts=counts,
        issues=issues,
        low_quality_project_names=low_quality_names,
    )


def _health_markdown(report: WikiLintReport, records: list[MemoryRecord], page_counts: dict[str, int]) -> str:
    lines = [
        "## Summary",
        f"- Active memories: `{len(records)}`",
        f"- Type pages: `{page_counts['type']}`",
        f"- Project pages: `{page_counts['project']}`",
        f"- Topic pages: `{page_counts['topic']}`",
        "",
        "## Issue Counts",
    ]
    if report.issue_counts:
        for issue_type, count in sorted(report.issue_counts.items()):
            lines.append(f"- `{issue_type}`: `{count}`")
    else:
        lines.append("- No issues detected.")

    if report.low_quality_project_names:
        lines.extend(["", "## Low Quality Project Names"])
        for item in report.low_quality_project_names[:20]:
            lines.append(
                f"- `{item.project_key}` -> `{item.display_name}` via `{item.source}`; "
                "consider adding a clearer bootstrap project name."
            )

    grouped: dict[str, list[WikiLintIssue]] = {}
    for issue in report.issues:
        grouped.setdefault(issue.issue_type, []).append(issue)
    if grouped:
        lines.extend(["", "## Issues By Type"])
        for issue_type, items in sorted(grouped.items()):
            lines.append(f"### {issue_type}")
            for item in items[:10]:
                lines.append(f"- {item.message}")
                if item.sample_titles:
                    lines.append(f"  Samples: {', '.join(item.sample_titles[:3])}")
                if item.suggestion:
                    lines.append(f"  Suggested action: {item.suggestion}")

    return _render_markdown("Wiki Health", lines)


def _build_pages(
    config: MemoryConfig,
    records: list[MemoryRecord],
    wiki_root: Path,
) -> tuple[dict[str, str], WikiLintReport, dict[str, ProjectDisplayNameResolverResult]]:
    project_names = _resolve_project_names(config, records)
    topic_records = _eligible_topics(records)

    provisional_page_keys = {
        "index",
        "health",
        "health.json",
        *[f"types/{item}" for item in sorted({record.memory_type for record in records})],
        *[f"projects/{item}/index" for item in project_names],
        *[f"topics/{slugify(tag)}" for tag in topic_records],
    }
    lint_report = _build_lint_report(records, topic_records, project_names, provisional_page_keys, wiki_root)

    pages: dict[str, str] = {}
    pages.update(_render_type_pages(records, project_names))
    pages.update(_render_project_pages(records, project_names, lint_report.issues))
    pages.update(_render_topic_pages(topic_records, project_names))
    pages["index"] = _render_index(records, project_names, topic_records)
    page_counts = {
        "type": len([key for key in pages if key.startswith("types/")]),
        "project": len([key for key in pages if key.startswith("projects/")]),
        "topic": len([key for key in pages if key.startswith("topics/")]),
    }
    pages["health"] = _health_markdown(lint_report, records, page_counts)
    pages["health.json"] = json.dumps(lint_report.to_dict(), ensure_ascii=False, indent=2) + "\n"
    return pages, lint_report, project_names


def _page_file_path(wiki_root: Path, page_key: str) -> Path:
    if page_key.endswith(".json"):
        return wiki_root / page_key
    return wiki_root / f"{page_key}.md"


def _write_if_changed(path: Path, content: str, state: WikiBuildState, page_key: str) -> bool:
    content_hash = _sha(content)
    if path.exists() and state.page_hashes.get(page_key) == content_hash:
        return False
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")
    return True


def build_wiki(config: MemoryConfig, store: MemoryStore, incremental: bool = True) -> dict[str, int | str | bool | dict[str, object]]:
    wiki_root = config.data_home_path / "wiki"
    wiki_root.mkdir(parents=True, exist_ok=True)
    state_path = wiki_root / ".state.json"
    previous_state = WikiBuildState.from_path(state_path) if incremental else WikiBuildState()

    active_records = _load_active_records(store)
    records = [item.record for item in active_records]
    pages, lint_report, project_names = _build_pages(config, records, wiki_root)

    eligible_topics = set(_eligible_topics(records).keys())
    new_record_state: dict[str, dict[str, object]] = {}
    for item in active_records:
        page_keys = _record_page_keys(item.record, eligible_topics)
        new_record_state[item.record.id] = {
            "source_path": str(item.source_path),
            "mtime_ns": item.source_mtime_ns,
            "page_keys": page_keys,
        }

    desired_page_keys = set(pages.keys())
    changed_pages: set[str] = set()
    if not previous_state.records:
        changed_pages = set(desired_page_keys)
        full_rebuild = True
    else:
        full_rebuild = False
        all_ids = set(new_record_state) | set(previous_state.records)
        for memory_id in all_ids:
            before = previous_state.records.get(memory_id)
            after = new_record_state.get(memory_id)
            if before != after:
                changed_pages.update((before or {}).get("page_keys", []))
                changed_pages.update((after or {}).get("page_keys", []))
        stale_page_keys = set(previous_state.page_hashes) - desired_page_keys
        changed_pages.update(stale_page_keys)
        if not changed_pages:
            changed_pages = {"health", "health.json"}

    stale_files = _stale_pages(wiki_root, desired_page_keys)
    for path in stale_files:
        path.unlink(missing_ok=True)

    written_files = 0
    page_hashes: dict[str, str] = {}
    for page_key, content in pages.items():
        page_hashes[page_key] = _sha(content)
        path = _page_file_path(wiki_root, page_key)
        if page_key in changed_pages or page_key in {"health", "health.json"}:
            if _write_if_changed(path, content, previous_state, page_key):
                written_files += 1
        elif not path.exists():
            if _write_if_changed(path, content, previous_state, page_key):
                written_files += 1

    state_payload = WikiBuildState(records=new_record_state, page_hashes=page_hashes)
    state_path.write_text(json.dumps(state_payload.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "wiki_root": str(wiki_root),
        "wiki_records": len(records),
        "wiki_type_pages": len([key for key in pages if key.startswith("types/")]),
        "wiki_project_pages": len([key for key in pages if key.startswith("projects/")]),
        "wiki_topic_pages": len([key for key in pages if key.startswith("topics/")]),
        "wiki_files": len(pages),
        "wiki_written_files": written_files,
        "wiki_full_rebuild": full_rebuild,
        "wiki_lint_summary": lint_report.summary(),
        "wiki_project_display_names": {key: value.display_name for key, value in project_names.items()},
    }


def lint_wiki(config: MemoryConfig, store: MemoryStore) -> dict[str, object]:
    wiki_root = config.data_home_path / "wiki"
    wiki_root.mkdir(parents=True, exist_ok=True)
    active_records = _load_active_records(store)
    records = [item.record for item in active_records]
    pages, lint_report, _project_names = _build_pages(config, records, wiki_root)
    health_md = _page_file_path(wiki_root, "health")
    health_json = _page_file_path(wiki_root, "health.json")
    health_md.write_text(pages["health"], encoding="utf-8")
    health_json.write_text(pages["health.json"], encoding="utf-8")
    return {
        "wiki_root": str(wiki_root),
        "wiki_records": len(records),
        **lint_report.summary(),
    }
