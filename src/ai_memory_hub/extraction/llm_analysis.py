"""
LLM 分析模块。

调用外部 LLM API 进行：
- 记忆标题生成
- 候选摘要压缩
- 记忆路由决策（project / inbox / rules）
- 矛盾检测
- 批量事件记忆提取

LLM 配置通过 config.json 的 `llm` 字段指定，不再依赖环境变量。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass(slots=True)
class LlmSettings:
    api_key: str
    base_url: str
    model: str
    timeout: int = 30


def load_llm_settings() -> LlmSettings | None:
    """
    从 config.json 加载 LLM 设置。

    如果 llm.enabled 为 False 或配置不完整，返回 None。
    """
    from ai_memory_hub.core.config import load_config
    try:
        cfg = load_config()
    except Exception:
        return None
    llm_cfg = cfg.llm
    if not llm_cfg.enabled or not llm_cfg.is_complete():
        return None
    return LlmSettings(
        api_key=llm_cfg.api_key,
        base_url=llm_cfg.base_url,
        model=llm_cfg.model,
        timeout=llm_cfg.timeout_seconds,
    )


def grounded_bullet_summary(*, title: str, candidates: list[str], max_items: int = 6) -> list[str]:
    settings = load_llm_settings()
    if settings is None or not candidates:
        return candidates[:max_items]

    prompt = _build_prompt(title=title, candidates=candidates, max_items=max_items)
    payload = {
        "model": settings.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个严格的成长沉淀分析器。"
                    "只能基于输入材料总结，不允许补充未出现事实。"
                    "必须过滤过程话术、任务执行日志、系统提示和不完整判断。"
                    "只保留可以长期复用、表述明确、可验证的结论。"
                    "输出必须是 JSON，格式为 {\"bullets\": [\"...\"]}。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    try:
        raw = _post_json(settings=settings, payload=payload)
        parsed = _extract_bullets(raw)
        return parsed[:max_items] if parsed else candidates[:max_items]
    except Exception:
        return candidates[:max_items]


def grounded_title(*, title: str, content: str, fallback: str, max_length: int = 36) -> str:
    settings = load_llm_settings()
    if settings is None or not content.strip():
        return fallback[:max_length]

    payload = {
        "model": settings.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个严格的信息提炼器。"
                    "只能基于输入内容生成一个简短标题，不允许补充新事实。"
                    "标题必须具体、去掉过程口吻、适合做知识库条目名。"
                    "输出必须是 JSON，格式为 {\"title\": \"...\"}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"任务：为\"{title}\"生成标题。\n"
                    f"要求：不超过 {max_length} 个字符；优先保留结论；禁止使用'我先/接下来/准备'等过程措辞。\n"
                    f"内容：\n{content}"
                ),
            },
        ],
        "temperature": 0.1,
    }
    try:
        raw = _post_json(settings=settings, payload=payload)
        parsed = _extract_title(raw)
        return (parsed or fallback)[:max_length]
    except Exception:
        return fallback[:max_length]


def grounded_keep_best(*, title: str, candidates: list[str], max_items: int = 8) -> list[str]:
    settings = load_llm_settings()
    if settings is None or not candidates:
        return candidates[:max_items]

    payload = {
        "model": settings.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个严格的记忆去噪器。"
                    "只能删除、合并、保留输入候选，不允许发明新事实。"
                    "输出必须是 JSON，格式为 {\"bullets\": [\"...\"]}。"
                ),
            },
            {
                "role": "user",
                "content": _build_prompt(title=title, candidates=candidates, max_items=max_items),
            },
        ],
        "temperature": 0.1,
    }
    try:
        raw = _post_json(settings=settings, payload=payload)
        parsed = _extract_bullets(raw)
        return parsed[:max_items] if parsed else candidates[:max_items]
    except Exception:
        return candidates[:max_items]


def grounded_route_decision(
    *,
    content: str,
    current_type: str,
    project_name: str | None,
    fallback_route: str,
) -> str:
    settings = load_llm_settings()
    if settings is None or not content.strip():
        return fallback_route

    payload = {
        "model": settings.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个保守的知识库归档助手。"
                    "只能在 project、inbox、rules 三种 route 中选择。"
                    "当信息不足、项目不清晰或内容更像候选时，必须选 inbox。"
                    "只有项目明确且内容类型稳定时，才能选 project。"
                    "只有跨项目通用且稳定的规则，才能选 rules。"
                    "输出必须是 JSON，格式为 {\"route\": \"project|inbox|rules\"}。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前类型: {current_type}\n"
                    f"项目: {project_name or 'unknown'}\n"
                    f"默认路由: {fallback_route}\n"
                    f"内容:\n{content}"
                ),
            },
        ],
        "temperature": 0.1,
    }
    try:
        raw = _post_json(settings=settings, payload=payload)
        route = _extract_route(raw)
        return route or fallback_route
    except Exception:
        return fallback_route


def _build_prompt(*, title: str, candidates: list[str], max_items: int) -> str:
    lines = "\n".join(f"- {item}" for item in candidates)
    return (
        f"任务：为\"{title}\"筛选并压缩候选条目。\n"
        f"要求：最多输出 {max_items} 条；必须真实、具体、可复用；不要输出过程描述；不要改写出输入中不存在的新事实。\n"
        "如果没有足够扎实的条目，就返回空数组。\n"
        "候选内容：\n"
        f"{lines}"
    )


def _post_json(*, settings: LlmSettings, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    endpoint = f"{settings.base_url}/chat/completions"
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=settings.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(message) from exc

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("No completion choices returned.")
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


def _extract_bullets(text: str) -> list[str]:
    match = re.search(r"\{[\s\S]*\}", text)
    raw = match.group(0) if match else text
    payload = json.loads(raw)
    bullets = payload.get("bullets") or []
    result: list[str] = []
    seen: set[str] = set()
    for item in bullets:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().strip("-").strip()
        lowered = cleaned.lower()
        if not cleaned or lowered in seen:
            continue
        seen.add(lowered)
        result.append(cleaned)
    return result


def _extract_title(text: str) -> str | None:
    match = re.search(r"\{[\s\S]*\}", text)
    raw = match.group(0) if match else text
    payload = json.loads(raw)
    title = payload.get("title")
    if not isinstance(title, str):
        return None
    cleaned = re.sub(r"\s+", " ", title).strip().strip("-").strip()
    return cleaned or None


def _extract_route(text: str) -> str | None:
    match = re.search(r"\{[\s\S]*\}", text)
    raw = match.group(0) if match else text
    payload = json.loads(raw)
    route = payload.get("route")
    if route not in {"project", "inbox", "rules"}:
        return None
    return route


def extract_memories_from_events(
    *,
    events: list[dict],
    max_items: int = 20,
) -> list[dict] | None:
    """
    将原始事件批量送入 LLM，提取候选记忆。
    events: [{"text": str, "source_tool": str, "project_key": str | None, "timestamp": str | None}, ...]
    返回: [{"title", "summary", "memory_type", "scope", "confidence", "stability", "tags", "hints"}, ...] 或 None（失败时）
    """
    settings = load_llm_settings()
    if settings is None:
        return None

    from collections import defaultdict
    grouped: dict[str, list[str]] = defaultdict(list)
    for event in events:
        text = (event.get("text") or "").strip()
        if len(text) < 10:
            continue
        grouped[event.get("source_tool", "unknown")].append(text[:500])

    all_texts: list[str] = []
    for tool, texts in grouped.items():
        snippet = "\n".join(f"[{tool}] {t}" for t in texts[:10])
        all_texts.append(snippet)

    if not all_texts:
        return None

    prompt = (
        "你是一个严格的用户记忆提炼器。\n"
        "从以下对话片段中，提取值得长期记住的用户偏好、规则、经验、模式。\n"
        "每条记忆必须是：\n"
        "  - 具体、明确、可验证\n"
        "  - 不含过程描述（'我会''我先''接下来'等过滤掉）\n"
        "  - 至少被 1 个片段支持\n\n"
        "对每条记忆输出：\n"
        "  title: 简短标题（≤36字，结论式）\n"
        "  summary: 摘要（≤72字）\n"
        "  memory_type: procedural|profile|semantic|episodic\n"
        "  scope: global|project\n"
        "  confidence: 0.0-1.0（越明确越高）\n"
        "  stability: 0.0-1.0（越稳定越高）\n"
        "  tags: [tag1, tag2]\n"
        "  hints: 简短说明为什么这条值得记住\n\n"
        f"最多输出 {max_items} 条。如果没有值得提炼的内容，返回空数组。\n\n"
        "对话片段：\n" +
        "\n---\n".join(all_texts)
    )

    payload = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": (
                "你是一个严格的用户记忆提炼器。"
                "只提取真实偏好、规则、经验，过滤掉任务请求、闲聊、过程描述。"
                "输出必须是 JSON，格式为 {\"candidates\": [...]}"
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }

    try:
        raw = _post_json(settings=settings, payload=payload)
        return _extract_candidates(raw)
    except Exception:
        return None


def _extract_candidates(text: str) -> list[dict] | None:
    match = re.search(r"\{[\s\S]*\}", text)
    raw = match.group(0) if match else text
    payload = json.loads(raw)
    candidates = payload.get("candidates") or []
    result: list[dict] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        if not title or len(title) < 4:
            continue
        lowered = title.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append({
            "title": title[:36],
            "summary": (item.get("summary") or title)[:72],
            "memory_type": item.get("memory_type", "semantic"),
            "scope": item.get("scope", "global"),
            "confidence": float(item.get("confidence", 0.6)),
            "stability": float(item.get("stability", 0.6)),
            "tags": list(item.get("tags", [])),
            "hints": (item.get("hints") or "")[:120],
        })
    return result


def detect_contradiction(
    *,
    memory_a: dict,
    memory_b: dict,
) -> bool | None:
    """
    判断两条记忆是否在含义上矛盾。
    返回 True（矛盾）、False（不矛盾）、None（无法判断/LLM不可用）。
    """
    settings = load_llm_settings()
    if settings is None:
        return None

    prompt = (
        "判断以下两条用户记忆是否矛盾。\n"
        "矛盾的定义：两条记忆同时成立会导致用户行为冲突或偏好不一致。\n"
        "A: {memory_a[title]} | {memory_a[summary]}\n"
        "B: {memory_b[title]} | {memory_b[summary]}\n"
        "回答格式：{{\"contradicts\": true|false, \"reason\": \"...\"}}"
    ).format(memory_a=memory_a, memory_b=memory_b)

    payload = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": "你是严格的知识库一致性检查器。只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.05,
    }

    try:
        raw = _post_json(settings=settings, payload=payload)
        match = re.search(r"\{[\s\S]*\}", raw)
        data = json.loads(match.group(0) if match else raw)
        return data.get("contradicts")
    except Exception:
        return None
