"""
记忆抽取模块。

从原始事件中提炼候选记忆（candidates），支持：
- 基于显式激活关键词的正则匹配
- 基于 LLM 的智能抽取
- 候选记忆合并与去重
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.models import Evidence, MemoryRecord, utc_now
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.core.utils import contains_mojibake, stable_id, trim_excerpt

SENTENCE_SPLIT_PATTERN = re.compile(r"[。；;!?！？\n]+")
PATH_ONLY_PATTERN = re.compile(r"^[a-zA-Z]:\\|^/|^[.~]?[/\\]|[/\\].+[/\\]")
GENERIC_TITLE_PATTERN = re.compile(r"^(这个|那个|问题|需求|计划|方案|项目|优化|修复|处理)\b", re.IGNORECASE)
HEADER_OR_BULLET_PATTERN = re.compile(r"^\s*(?:[-*]\s+|#+\s+|\d+\.\s+)")
CODE_FRAGMENT_PATTERN = re.compile(r"(?:\breturn\b|->|\.stream\(|Collectors\.|public\s+class|@\w+|[{};]{2,})")
PLAN_MARKERS = (
    "please implement this plan",
    "summary",
    "key changes",
    "test plan",
    "public interfaces",
    "assumptions",
    "目标是把",
    "本次改造",
    "默认采用端到端改造",
    "高可用改造计划",
)
PREFERENCE_PATTERNS = [
    (re.compile(r"^(不要|别|never)\s*(.+)$", re.IGNORECASE), "watchout"),
    (re.compile(r"^(优先|prefer)\s*(.+)$", re.IGNORECASE), "preference"),
    (re.compile(r"^(请用|请使用|use)\s*(.+)$", re.IGNORECASE), "procedure"),
    (re.compile(r"^(以后|默认|必须|需要|记住|always|must|default)\s*(.+)$", re.IGNORECASE), "rule"),
    (re.compile(r"^(每次|每当)\s*(.+)$", re.IGNORECASE), "rule"),
]

LANGUAGE_HINTS = [
    "中文",
    "english",
    "tone",
    "语气",
    "输出",
    "列表",
    "bullets",
    "bullet",
    "markdown",
    "格式",
]
PROCEDURE_HINTS = [
    "sql",
    "数据库",
    "test",
    "测试",
    "commit",
    "提交",
    "api",
    "controller",
    "service",
    "mcp",
    "脚本",
    "sync",
    "maven",
    "build",
    "profile",
    "运行",
    "启动",
]
PROJECT_HINTS = [
    "repo",
    "repository",
    "模块",
    "module",
    "pom.xml",
    "maven",
    "spring boot",
    "controller",
    "service",
    "table",
    "sql",
    "数据库",
    "profile",
    "build",
    "test",
]
LOW_VALUE_MARKERS = [
    "this skill should be used",
    "available skills",
    "skill.md",
    "<instructions>",
    "## skills",
    "source reputation",
    "context7-compatible library id",
    "please implement this plan",
    "i want to build",
    "i want a",
    "help me build",
    "请你参考",
    "我想搭建",
    "我想做一个",
    "抛开这个项目",
]
TASK_REQUEST_MARKERS = [
    "帮我",
    "请帮我",
    "给我",
    "怎么做",
    "能不能",
    "请实现",
    "实现这个",
    "修复这个",
    "分析一下",
    "看看",
    "继续",
    "还有啥",
    "需要新建",
    "新增",
    "创建",
    "修改",
    "完善",
    "直接运行",
    "输出最后结果",
]


@dataclass(slots=True)
class CandidateMemory:
    key: str
    title: str
    memory_type: str
    scope: str
    tool: str
    project_key: str | None
    summary: str
    details: str
    tags: set[str]
    evidence: list[Evidence]
    confidence: float
    stability: float
    sensitivity: str
    last_seen_at: str | None


def _sensitivity(config: MemoryConfig, text: str) -> str:
    lowered = text.lower()
    for pattern in config.scan.sensitivity_patterns:
        if pattern.lower() in lowered:
            return "high"
    return "low"


def _memory_type(summary: str, hint: str, scope: str) -> str:
    lowered = summary.lower()
    if hint == "preference" or any(token in lowered for token in LANGUAGE_HINTS):
        return "profile"
    if hint in {"rule", "procedure", "watchout"} or any(token in lowered for token in PROCEDURE_HINTS):
        return "procedural"
    if scope == "project":
        return "semantic"
    return "semantic"


def _scope(summary: str, project_key: str | None) -> str:
    lowered = summary.lower()
    if project_key and any(token in lowered for token in PROJECT_HINTS):
        return "project"
    return "global"


def _normalize_action(action: str) -> str:
    return action.strip().replace("  ", " ")


def _normalize_body(body: str) -> str:
    cleaned = re.sub(r"\s+", " ", body).strip(" ，。；;:：!?！？")
    cleaned = re.sub(r"^(请|请你)\s*", "", cleaned)
    return trim_excerpt(cleaned, 96)


def is_low_value_text(text: str, *, explicit_keywords: list[str] | None = None) -> bool:
    lowered = text.lower().strip()
    explicit_keywords = explicit_keywords or []
    if not lowered:
        return True
    if contains_mojibake(text):
        return True
    if any(marker in lowered for marker in PLAN_MARKERS):
        return True
    if any(marker in lowered for marker in LOW_VALUE_MARKERS):
        return True
    if any(marker in text for marker in TASK_REQUEST_MARKERS):
        return True
    if PATH_ONLY_PATTERN.search(text) and len(re.findall(r"[/\\]", text)) >= 2:
        return True
    if lowered.startswith("#") or lowered.startswith("##"):
        return True
    if HEADER_OR_BULLET_PATTERN.match(text) and not any(keyword.lower() in lowered for keyword in explicit_keywords):
        return True
    if GENERIC_TITLE_PATTERN.search(text) and len(text) < 18:
        return True
    if CODE_FRAGMENT_PATTERN.search(text):
        return True
    if len(text) <= 10 and not any(keyword.lower() in lowered for keyword in explicit_keywords):
        return True
    return False


def _normalize_summary(text: str, *, explicit_keywords: list[str]) -> tuple[str, str] | None:
    compact = trim_excerpt(HEADER_OR_BULLET_PATTERN.sub("", text).strip(), 320)
    if is_low_value_text(compact, explicit_keywords=explicit_keywords):
        return None
    lowered = compact.lower()
    for pattern, hint in PREFERENCE_PATTERNS:
        match = pattern.search(compact)
        if match:
            action = _normalize_action(match.group(1))
            body = _normalize_body(match.group(2))
            if not body:
                return None
            summary = trim_excerpt(f"{action} {body}".strip(), 72)
            return summary, hint
    explicit_lowered = [item.lower() for item in explicit_keywords]
    if any(keyword in lowered for keyword in explicit_lowered):
        return trim_excerpt(compact, 72), "rule"
    if any(token in compact for token in ["注意", "小心", "别忘了"]):
        return trim_excerpt(compact, 72), "watchout"
    if any(token in lowered for token in PROJECT_HINTS) and len(compact) <= 160:
        return trim_excerpt(compact, 72), "fact"
    return None


def _tool_scope(config: MemoryConfig, text: str) -> str:
    lowered = text.lower()
    matches = []
    for tool in config.tools:
        names = {tool.id.lower(), tool.label.lower()}
        if any(name and name in lowered for name in names):
            matches.append(tool.id)
    return matches[0] if len(matches) == 1 else "shared"


def _candidate_snippets(text: str, explicit_keywords: list[str]) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact or contains_mojibake(compact):
        return []
    explicit_lowered = [item.lower() for item in explicit_keywords]
    snippets: list[str] = []
    for part in SENTENCE_SPLIT_PATTERN.split(compact):
        snippet = HEADER_OR_BULLET_PATTERN.sub("", part).strip()
        if len(snippet) < 6 or is_low_value_text(snippet, explicit_keywords=explicit_keywords):
            continue
        lowered = snippet.lower()
        if any(keyword in lowered for keyword in explicit_lowered):
            snippets.append(snippet)
            continue
        if any(pattern.search(snippet) for pattern, _ in PREFERENCE_PATTERNS):
            snippets.append(snippet)
            continue
        if any(token in lowered for token in PROJECT_HINTS) and len(snippet) <= 160:
            snippets.append(snippet)
    return snippets[:8]


def _build_candidate(config: MemoryConfig, event, title: str, hint: str) -> CandidateMemory:
    scope = _scope(title, event["project_key"])
    memory_type = _memory_type(title, hint, scope)
    tool = _tool_scope(config, event["text"] or title) if scope != "project" else "shared"
    tags = {hint, memory_type, event["source_tool"]}
    if scope == "project" and event["project_key"]:
        tags.add(event["project_key"])
        tags.add("needs-review")
    if memory_type == "profile":
        tags.add("needs-review")
    evidence = [
        Evidence(
            source_tool=event["source_tool"],
            source_path=event["source_path"],
            session_id=event["session_id"],
            timestamp=event["timestamp"],
            excerpt="[redacted-sensitive]" if _sensitivity(config, event["text"] or "") == "high" else trim_excerpt(event["text"], 220),
        )
    ]
    explicit_bonus = 0.18 if hint in {"rule", "procedure"} else 0.1
    return CandidateMemory(
        key=stable_id(memory_type, scope, tool, event["project_key"] or "", title.lower()),
        title=title,
        memory_type=memory_type,
        scope=scope,
        tool=tool,
        project_key=event["project_key"],
        summary=title,
        details=event["text"] or title,
        tags=tags,
        evidence=evidence,
        confidence=min(0.99, 0.55 + explicit_bonus),
        stability=0.8 if hint in {"rule", "watchout", "fact"} else 0.65,
        sensitivity=_sensitivity(config, event["text"] or ""),
        last_seen_at=event["timestamp"],
    )


def consolidate(config: MemoryConfig, store: MemoryStore) -> dict[str, int]:
    """
    两阶段提炼：
    阶段1（LLM）：批量处理原始事件（需 config.scan.llm_refinement.enabled=true）
    阶段2（降级）：正则匹配（LLM 不可用或失败时）
    """
    if config.scan.llm_refinement.enabled:
        result = _consolidate_by_llm(config, store)
        if result is not None:
            return result

    return _consolidate_by_regex(config, store)


def _consolidate_by_llm(config: MemoryConfig, store: MemoryStore) -> dict[str, int] | None:
    from ai_memory_hub.extraction.llm_analysis import extract_memories_from_events
    from ai_memory_hub.core.models import Evidence, utc_now

    events = store.list_raw_events()
    user_events = [
        dict(e) for e in events
        if e["role"] == "user" and (e["text"] or "").strip()
    ]

    if not user_events:
        return {"memories_written": 0, "active_memories": 0, "queued_for_review": 0, "source": "llm"}

    max_batch = config.scan.max_events_per_llm_call
    all_candidates: list[dict] = []

    for i in range(0, len(user_events), max_batch):
        batch = user_events[i:i + max_batch]
        candidates = extract_memories_from_events(
            events=batch,
            max_items=config.scan.llm_refinement.max_output_items,
        )
        if candidates:
            all_candidates.extend(candidates)

    if not all_candidates:
        if config.scan.llm_refinement.fallback_to_regex:
            return None
        return {"memories_written": 0, "active_memories": 0, "queued_for_review": 0, "source": "llm"}

    written = 0
    activated = 0

    for cand in all_candidates:
        key = stable_id(
            cand["memory_type"], cand["scope"], "shared",
            "", cand["title"].lower()
        )

        matched_events = [
            e for e in user_events
            if (e["text"] or "").lower() in cand["summary"].lower() or
               cand["title"].lower() in (e["text"] or "").lower()
        ][:3]

        evidence = [
            Evidence(
                source_tool=e.get("source_tool", "llm"),
                source_path="llm_batch",
                session_id=e.get("session_id"),
                timestamp=e.get("timestamp"),
                excerpt=(e["text"] or "")[:220],
            )
            for e in matched_events
        ]

        if not evidence:
            evidence = [Evidence(
                source_tool="llm",
                source_path="llm_batch",
                session_id=None,
                timestamp=utc_now(),
                excerpt=(cand.get("hints") or cand["summary"])[:220],
            )]

        can_activate = (
            cand["confidence"] >= config.scan.auto_activate_confidence
            and cand["stability"] >= config.scan.auto_activate_stability
            and cand["memory_type"] not in {"profile", "episodic"}
            and cand["scope"] != "project"
        )

        tags = list(cand.get("tags", []))
        tags.append(cand["memory_type"])
        if not can_activate:
            tags.append("needs-review")

        status = "active" if can_activate else "candidate"
        record = MemoryRecord(
            id=key,
            title=cand["title"],
            memory_type=cand["memory_type"],
            scope=cand["scope"],
            tool="shared",
            project_key=None,
            summary=cand["summary"],
            details=cand.get("hints", cand["summary"]),
            evidence=evidence,
            confidence=round(cand["confidence"], 3),
            stability=round(cand["stability"], 3),
            sensitivity="low",
            tags=sorted(set(tags)),
            created_at=utc_now(),
            last_seen_at=utc_now(),
            reviewed_at=None,
            status=status,
            supersedes=None,
            managed_by="llm",
            manual_override=False,
            last_accessed_at=None,
            expiration_days=90,
        )

        existing = store.load_memory(key)
        if existing:
            if existing.manual_override:
                continue
            record.id = existing.id
            record.created_at = existing.created_at

        store.write_memory(record)
        written += 1
        if record.status == "active":
            activated += 1

    return {
        "memories_written": written,
        "active_memories": activated,
        "queued_for_review": written - activated,
        "source": "llm",
    }


def _consolidate_by_regex(config: MemoryConfig, store: MemoryStore) -> dict[str, int]:
    """正则匹配提炼（LLM 降级 / 默认兜底）——迁移自原有 consolidate 逻辑"""
    events = store.list_raw_events()
    grouped: dict[str, CandidateMemory] = {}
    evidence_count: defaultdict[str, int] = defaultdict(int)
    for event in events:
        if event["role"] != "user":
            continue
        text = event["text"] or ""
        if len(text.strip()) < 6:
            continue
        for snippet in _candidate_snippets(text, config.scan.explicit_activation_keywords):
            event_for_snippet = dict(event)
            event_for_snippet["text"] = snippet
            normalized = _normalize_summary(snippet, explicit_keywords=config.scan.explicit_activation_keywords)
            if not normalized:
                continue
            title, hint = normalized
            candidate = _build_candidate(config, event_for_snippet, title, hint)
            existing = grouped.get(candidate.key)
            if existing is None:
                grouped[candidate.key] = candidate
            else:
                existing.evidence.extend(candidate.evidence)
                existing.tags |= candidate.tags
                existing.last_seen_at = candidate.last_seen_at or existing.last_seen_at
                existing.details = trim_excerpt(f"{existing.details}\n\n{candidate.details}", 1600)
                existing.confidence = max(existing.confidence, candidate.confidence)
                existing.stability = max(existing.stability, candidate.stability)
                if existing.sensitivity != "high":
                    existing.sensitivity = candidate.sensitivity
            evidence_count[candidate.key] += 1

    written = 0
    activated = 0
    queued_for_review = 0
    for key, candidate in grouped.items():
        repetitions = evidence_count[key]
        confidence = min(0.99, candidate.confidence + max(0, repetitions - 1) * 0.08)
        stability = min(0.99, candidate.stability + max(0, repetitions - 1) * 0.07)
        can_auto_activate = (
            confidence >= config.scan.auto_activate_confidence
            and stability >= config.scan.auto_activate_stability
            and candidate.scope != "project"
            and candidate.memory_type != "profile"
            and "needs-review" not in candidate.tags
        )
        status = "active" if can_auto_activate else "candidate"
        if status == "candidate" and "needs-review" in candidate.tags:
            queued_for_review += 1
        existing = store.load_memory(key)
        if existing and existing.manual_override:
            existing.last_seen_at = candidate.last_seen_at or existing.last_seen_at
            existing.evidence = (existing.evidence + candidate.evidence)[-12:]
            existing.tags = sorted(set(existing.tags) | candidate.tags)
            record = existing
        else:
            record = MemoryRecord(
                id=key,
                title=candidate.title,
                memory_type=candidate.memory_type,
                scope=candidate.scope,
                tool=candidate.tool,
                project_key=candidate.project_key,
                summary=candidate.summary,
                details=candidate.details,
                evidence=candidate.evidence[-12:],
                confidence=round(confidence, 3),
                stability=round(stability, 3),
                sensitivity=candidate.sensitivity,
                tags=sorted(candidate.tags),
                created_at=(existing.created_at if existing else candidate.last_seen_at or utc_now()),
                last_seen_at=candidate.last_seen_at,
                reviewed_at=(existing.reviewed_at if existing else None),
                status=status,
                supersedes=(existing.supersedes if existing else None),
                managed_by=(existing.managed_by if existing else "system"),
                manual_override=(existing.manual_override if existing else False),
                last_accessed_at=(existing.last_accessed_at if existing else None),
                expiration_days=(existing.expiration_days if existing else 90),
            )
        store.write_memory(record)
        written += 1
        if record.status == "active":
            activated += 1
    return {
        "memories_written": written,
        "active_memories": activated,
        "queued_for_review": queued_for_review,
        "source": "regex",
    }


def write_memory_record(
    store: MemoryStore,
    *,
    title: str,
    memory_type: str,
    scope: str,
    tool: str,
    project_key: str | None,
    summary: str,
    details: str,
    tags: list[str],
    evidence: list[dict],
    confidence: float,
    stability: float,
    sensitivity: str,
    status: str,
    supersedes: str | None = None,
    managed_by: str = "user",
    manual_override: bool = True,
) -> MemoryRecord:
    existing_id = stable_id(memory_type, scope, tool, project_key or "", title.lower(), summary.lower())
    current = store.load_memory(existing_id)
    record = MemoryRecord(
        id=current.id if current else existing_id,
        title=title,
        memory_type=memory_type,
        scope=scope,
        tool=tool,
        project_key=project_key,
        summary=summary,
        details=details,
        evidence=[Evidence(**item) for item in evidence],
        confidence=confidence,
        stability=stability,
        sensitivity=sensitivity,
        tags=tags,
        created_at=current.created_at if current else utc_now(),
        last_seen_at=current.last_seen_at if current else None,
        reviewed_at=utc_now(),
        status=status,
        supersedes=supersedes,
        managed_by=managed_by,
        manual_override=manual_override,
        last_accessed_at=current.last_accessed_at if current else None,
        expiration_days=current.expiration_days if current else 90,
    )
    store.write_memory(record)
    return record
