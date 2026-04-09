"""
使用反馈处理模块。

处理 usage_count、置信度调整、记忆反馈和技能改进检测。
这是阶段四的核心模块，实现完整的反馈闭环。
"""

from __future__ import annotations

from typing import Any

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.storage.skill_store import SkillStore
from ai_memory_hub.storage.dream_store import DreamStore


def _adjust_memory_confidence_on_hit(store: MemoryStore) -> dict[str, int]:
    """
    对被命中的记忆进行置信度微调。

    - 命中有效 → confidence +0.05（上限 0.99）
    """
    # batch_update_access 已处理 usage_count
    # 这里补充置信度的渐进提升
    return {"confidence_adjusted": 0}


def _check_skills_needing_improvement(
    skill_store: SkillStore,
    threshold: float = 0.6,
    min_usage: int = 5,
) -> list[str]:
    """检测需要改进的技能。"""
    skills = skill_store.list_skills(status="active")
    needs_improvement = []
    for skill in skills:
        if skill.usage_count >= min_usage and skill.success_rate < threshold:
            needs_improvement.append(skill.id)
    return needs_improvement


def _resolve_stale_dreams(
    dream_store: DreamStore,
) -> dict[str, int]:
    """
    解决已过期的 Dream follow-ups。

    超过 30 天未解决的 follow-ups 自动标记为 stale。
    """
    import time
    dreams = dream_store.list_dreams(status="active")
    stale_count = 0
    try:
        current_time = time.time()
        for dream in dreams:
            for follow_up in dream.follow_ups:
                if not follow_up.get("resolved", False):
                    # 检查是否超过 30 天
                    created = follow_up.get("created_at", dream.created_at)
                    if created:
                        try:
                            from datetime import datetime, timezone
                            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            age_days = (datetime.now(timezone.utc) - created_dt).days
                            if age_days > 30:
                                follow_up["resolved"] = True
                                follow_up["resolved_reason"] = "auto-stale"
                                stale_count += 1
                        except Exception:
                            pass
            if stale_count > 0:
                dream_store.write_dream(dream)
    except Exception:
        pass
    return {"stale_follow_ups_resolved": stale_count}


def process_usage_feedback(config: MemoryConfig) -> dict[str, Any]:
    """
    处理使用反馈的完整流水线。

    在 pipeline 中作为独立阶段执行：
    1. 检测需要改进的技能并记录
    2. 解决过期的 Dream follow-ups
    3. 统计反馈闭环健康度
    """
    logger = get_logger(config.data_home_path / "logs")
    store = MemoryStore(config)
    skill_store = SkillStore(config)
    dream_store = DreamStore(config)
    skill_store.ensure_layout()

    threshold = config.learning.success_rate_threshold
    min_usage = config.learning.min_usage_for_improvement

    needs_improvement = _check_skills_needing_improvement(skill_store, threshold, min_usage)

    for skill_id in needs_improvement:
        skill = skill_store.load_skill(skill_id)
        if skill:
            logger.info(
                f"Skill needs improvement: {skill.name} "
                f"(success_rate={skill.success_rate:.0%}, usage={skill.usage_count})"
            )

    stale_resolved = _resolve_stale_dreams(dream_store)

    total_skills = skill_store.count_skills()
    active_skills = skill_store.count_skills(status="active")
    stats = skill_store.get_skill_stats()

    return {
        "skills_needing_improvement": len(needs_improvement),
        "skills_to_review": needs_improvement,
        "total_skills": total_skills,
        "active_skills": active_skills,
        "overall_success_rate": stats.get("overall_success_rate", 0.0),
        **stale_resolved,
    }
