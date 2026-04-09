"""
技能抽取模块。

从重复任务、Dream、Memory 中自动抽取技能。
触发条件：
- 同一类任务完成 >= 3 次
- Dream 中关键决策出现 >= 3 次
- 用户显式指令
"""

from __future__ import annotations

import re
from typing import Any

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.core.models import utc_now
from ai_memory_hub.core.skill_models import Skill, SkillStep
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.storage.dream_store import DreamStore
from ai_memory_hub.storage.skill_store import SkillStore


# 技能抽取关键词（用户显式指令）
SKILL_EXTRACTION_KEYWORDS = [
    "把这个固化成技能",
    "把这个流程固化为技能",
    "create a skill",
    "extract as skill",
    "save as skill",
    "固化",
    "保存为技能",
]


# 决策关键词（识别关键决策）
DECISION_KEYWORDS = [
    "决定", "采用", "选择", "我们用", "使用",
    "decide", "adopt", "choose", "use", "we will",
    "结论是", "因此", "最终", "选择方案",
]


def _is_skill_extraction_request(text: str) -> bool:
    """检测是否包含技能抽取指令。"""
    text_lower = text.lower()
    return any(kw in text_lower for kw in SKILL_EXTRACTION_KEYWORDS)


def _extract_decisions(text: str) -> list[str]:
    """从文本中提取关键决策。"""
    decisions = []
    for keyword in DECISION_KEYWORDS:
        if keyword.lower() in text.lower():
            sentences = re.split(r"[。.!?\n]", text)
            for sentence in sentences:
                if keyword.lower() in sentence.lower() and len(sentence.strip()) > 10:
                    decisions.append(sentence.strip()[:200])
    seen = set()
    unique = []
    for d in decisions:
        normalized = d.lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(d)
    return unique


def _detect_task_category(events: list[dict[str, Any]]) -> str:
    """检测任务类别。"""
    all_text = " ".join(
        e.get("text", "") or e.get("content", "")
        for e in events
    ).lower()

    if any(k in all_text for k in ["bug", "error", "修复", "调试", "fix"]):
        return "debug"
    if any(k in all_text for k in ["创建", "实现", "写", "build", "create", "implement"]):
        return "creation"
    if any(k in all_text for k in ["学习", "理解", "研究", "learn", "understand"]):
        return "learning"
    if any(k in all_text for k in ["决策", "选择方案", "architecture", "设计"]):
        return "decision"
    if any(k in all_text for k in ["测试", "test", "验证", "verify"]):
        return "testing"
    if any(k in all_text for k in ["部署", "deploy", "发布", "release"]):
        return "deployment"
    return "general"


def _generate_skill_name_from_events(events: list[dict[str, Any]]) -> str:
    """从事件序列中生成技能名称。"""
    category = _detect_task_category(events)
    all_text = " ".join(
        e.get("text", "") or e.get("content", "")
        for e in events
    )

    name_parts = []
    user_msgs = [
        e.get("text", "") or e.get("content", "")
        for e in events
        if e.get("role") == "user" and len(e.get("text", "") or "") > 5
    ]
    if user_msgs:
        first_msg = user_msgs[0][:60]
        name_parts.append(first_msg.replace("\n", " ").strip())

    category_map = {
        "debug": "调试",
        "creation": "构建",
        "learning": "学习",
        "decision": "决策",
        "testing": "测试",
        "deployment": "部署",
        "general": "任务",
    }
    if not name_parts:
        return f"{category_map.get(category, '任务')}技能"
    return name_parts[0]


def _extract_code_snippets(events: list[dict[str, Any]]) -> list[str]:
    """从事件中提取代码片段。"""
    code_pattern = re.compile(r"```(\w*)\n([\s\S]*?)```")
    snippets = []
    for e in events:
        text = e.get("text", "") or e.get("content", "") or ""
        for match in code_pattern.finditer(text):
            code = match.group(2).strip()
            if len(code) > 20:
                snippets.append(code[:500])
    return snippets[:10]


def _deduce_required_tools(events: list[dict[str, Any]]) -> list[str]:
    """从事件中推断所需工具。"""
    all_text = " ".join(
        e.get("text", "") or e.get("content", "")
        for e in events
    ).lower()

    tool_keywords = {
        "git": ["git", "commit", "branch", "push", "pull"],
        "shell": ["bash", "sh", "powershell", "cmd", "terminal", "终端"],
        "docker": ["docker", "container", "镜像", "容器"],
        "npm": ["npm", "node_modules", "package.json"],
        "python": ["python", "pip", "venv", "__pycache__"],
        "api": ["api", "http", "rest", "endpoint", "接口"],
        "database": ["sql", "database", "mysql", "postgres", "sqlite", "数据库"],
        "docker-compose": ["docker-compose", "compose"],
    }

    detected = []
    for tool, keywords in tool_keywords.items():
        if any(kw in all_text for kw in keywords):
            detected.append(tool)
    return detected


def _suggest_tags_from_events(events: list[dict[str, Any]]) -> list[str]:
    """从事件中推断标签。"""
    all_text = " ".join(
        e.get("text", "") or e.get("content", "")
        for e in events
    ).lower()

    tag_patterns = {
        "fastapi": ["fastapi", "uvicorn"],
        "react": ["react", "jsx", "tsx", "useeffect", "usestate"],
        "python": ["python", "pip", "venv"],
        "typescript": ["typescript", "ts-", "interface", "type "],
        "git": ["git", "commit", "branch"],
        "docker": ["docker", "container"],
        "api": ["api", "endpoint", "rest"],
        "database": ["sql", "database", "query"],
        "testing": ["test", "pytest", "unittest", "测试"],
        "debug": ["debug", "error", "exception", "bug"],
        "deployment": ["deploy", "ci/cd", "github actions", "部署"],
        "config": ["config", "yaml", "json", "toml", "配置"],
        "frontend": ["css", "html", "ui", "component", "组件"],
        "backend": ["server", "api", "controller", "service"],
    }

    detected = []
    for tag, keywords in tag_patterns.items():
        if any(kw in all_text for kw in keywords):
            detected.append(tag)
    return detected[:8]


def extract_skill_from_events(
    events: list[dict[str, Any]],
    skill_name: str | None = None,
    skill_trigger: str | None = None,
    config: MemoryConfig | None = None,
) -> Skill | None:
    """
    从事件序列中抽取技能。

    适用于：
    - 用户显式指令固化流程
    - 重复任务模式识别
    """
    if len(events) < 4:
        return None

    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()

    skill_store = SkillStore(config)
    category = _detect_task_category(events)
    decisions = []
    for e in events:
        text = e.get("text", "") or e.get("content", "") or ""
        decisions.extend(_extract_decisions(text))

    if len(decisions) < 1:
        return None

    steps = []
    for i, decision in enumerate(decisions[:10], 1):
        steps.append(SkillStep(
            order=i,
            instruction=decision,
        ))

    code_snippets = _extract_code_snippets(events)
    if code_snippets and not steps:
        steps.append(SkillStep(
            order=1,
            instruction=f"执行代码: {code_snippets[0][:100]}",
        ))

    if not steps:
        return None

    name = skill_name or _generate_skill_name_from_events(events)
    trigger = skill_trigger or f"执行{category}相关任务"

    skill = skill_store.create_skill(
        name=name,
        trigger=trigger,
        description=f"从 {len(events)} 条事件抽取的 {category} 技能，包含 {len(steps)} 个步骤",
        steps=steps,
        tools_required=_deduce_required_tools(events),
        tags=_suggest_tags_from_events(events) + [category],
        source_task_type=category,
        initial_confidence=0.5,
    )

    return skill


def check_skill_extraction_trigger(events: list[dict[str, Any]]) -> bool:
    """检查事件序列是否包含技能抽取触发指令。"""
    for e in events:
        text = e.get("text", "") or e.get("content", "") or ""
        if _is_skill_extraction_request(text):
            return True
    return False


def extract_skills_from_repeated_pattern(
    config: MemoryConfig | None = None,
    min_repetitions: int = 3,
) -> list[Skill]:
    """
    从重复模式中抽取技能。

    分析所有 Dream，识别相同类别的重复洞见，
    如果同一类别的洞见出现次数 >= min_repetitions，则抽取为技能。
    """
    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()

    logger = get_logger(config.data_home_path / "logs")
    skill_store = SkillStore(config)
    dream_store = DreamStore(config)
    dreams = dream_store.list_dreams(status="active")

    category_insights: dict[str, list[dict[str, Any]]] = {}
    for dream in dreams:
        category = dream.category
        if category not in category_insights:
            category_insights[category] = []
        for insight in dream.insights:
            category_insights[category].append({
                "insight": insight,
                "dream_id": dream.id,
                "dream_title": dream.title,
            })

    extracted = []
    for category, insight_list in category_insights.items():
        if len(insight_list) < min_repetitions:
            continue

        insight_texts = [item["insight"] for item in insight_list]
        common_keywords = _find_common_keywords(insight_texts)

        if not common_keywords:
            continue

        steps = []
        for insight_text in insight_texts[:5]:
            if len(steps) >= 5:
                break
            steps.append(SkillStep(
                order=len(steps) + 1,
                instruction=insight_text[:200],
            ))

        skill = skill_store.create_skill(
            name=f"{category.title()} 技能",
            trigger=f"执行 {category} 相关任务",
            description=f"从 {len(insight_list)} 个 Dream 的洞见中抽取的 {category} 技能",
            steps=steps,
            tags=[category] + list(common_keywords)[:3],
            source_task_type=category,
            initial_confidence=0.5,
        )
        skill.source_dream_ids = list({item["dream_id"] for item in insight_list})
        skill_store.write_skill(skill)
        extracted.append(skill)
        logger.info(f"Extracted skill from repeated pattern: {skill.id} ({category})")

    return extracted


def _find_common_keywords(texts: list[str]) -> set[str]:
    """从多个文本中找到共同关键词。"""
    if not texts:
        return set()

    all_words: dict[str, int] = {}
    for text in texts:
        words = re.findall(r"[\w]{3,}", text.lower())
        for word in words:
            all_words[word] = all_words.get(word, 0) + 1

    min_count = len(texts) // 2
    return {w for w, c in all_words.items() if c >= min_count and len(w) >= 3}


def run_skill_extraction(config: MemoryConfig | None = None) -> dict[str, Any]:
    """
    执行技能抽取流水线。

    1. 检查显式抽取触发
    2. 从重复 Dream 洞见中抽取
    """
    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()

    logger = get_logger(config.data_home_path / "logs")
    store = MemoryStore(config)
    skill_store = SkillStore(config)
    skill_store.ensure_layout()

    logger.info("Starting skill extraction...")

    # 从重复模式抽取
    from_repeated = extract_skills_from_repeated_pattern(config, min_repetitions=3)
    repeated_count = len(from_repeated)

    total = skill_store.count_skills()
    active = skill_store.count_skills(status="active")
    draft = skill_store.count_skills(status="draft")

    logger.info(f"Skill extraction completed: {repeated_count} skills extracted")

    return {
        "extracted_from_repeated": repeated_count,
        "total_skills": total,
        "active_skills": active,
        "draft_skills": draft,
        "status": "completed",
    }
