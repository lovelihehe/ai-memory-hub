"""
技能服务层。

提供技能的管理、检索、匹配和改进功能：
- 技能 CRUD 操作
- 技能检索与匹配
- 技能使用反馈处理
- 技能自我改进（基于反馈）
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.core.models import utc_now
from ai_memory_hub.core.skill_models import Skill, SkillExample, SkillStep
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.storage.dream_store import DreamStore
from ai_memory_hub.storage.skill_store import SkillStore


class SkillService:
    """技能管理服务。"""

    def __init__(self, config: MemoryConfig):
        self.config = config
        self.logger = get_logger(config.data_home_path / "logs")
        self._store = MemoryStore(config)
        self._dream_store = DreamStore(config)
        self._skill_store = SkillStore(config)
        self._skill_store.ensure_layout()

    # ── 检索与匹配 ─────────────────────────────────────────

    def match_skills(self, task_description: str, limit: int = 5) -> list[dict[str, Any]]:
        """
        为任务检索匹配技能。

        匹配策略：
        1. 关键词匹配（trigger + tags + name）
        2. 向量相似度（如果可用）
        3. 成功率加权排序
        """
        keywords = self._extract_keywords(task_description)
        all_skills = self._skill_store.list_skills(status="active")

        scored: list[tuple[float, Skill]] = []
        for skill in all_skills:
            score = self._calculate_match_score(skill, keywords, task_description)
            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda item: -item[0])
        results = []
        for score, skill in scored[:limit]:
            self._skill_store.record_usage(skill.id)
            results.append({
                "skill_id": skill.id,
                "name": skill.name,
                "trigger": skill.trigger,
                "description": skill.description,
                "match_score": round(score, 3),
                "success_rate": round(skill.success_rate, 3),
                "usage_count": skill.usage_count,
                "confidence": skill.confidence,
                "tags": skill.tags,
                "version": skill.version,
                "markdown_content": skill.to_markdown(),
            })
        return results

    def _extract_keywords(self, text: str) -> set[str]:
        """从任务描述中提取关键词。"""
        text_lower = text.lower()
        words = re.findall(r"[\w]+", text_lower)
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "can", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into", "through",
            "我", "的", "是", "在", "和", "了", "要", "做", "请", "一个",
        }
        return {w for w in words if len(w) >= 3 and w not in stop_words}

    def _calculate_match_score(
        self,
        skill: Skill,
        keywords: set[str],
        task_description: str,
    ) -> float:
        """计算技能与任务的匹配分数。"""
        score = 0.0

        trigger_words = set(re.findall(r"[\w]+", skill.trigger.lower()))
        name_words = set(re.findall(r"[\w]+", skill.name.lower()))
        desc_words = set(re.findall(r"[\w]+", skill.description.lower()))

        skill_words = trigger_words | name_words | desc_words
        overlap = keywords & skill_words
        if overlap:
            score += len(overlap) * 0.3

        if any(kw in skill.trigger.lower() for kw in keywords):
            score += 1.5
        if any(kw in skill.name.lower() for kw in keywords):
            score += 1.0
        if any(kw in skill.description.lower() for kw in keywords):
            score += 0.5

        for tag in skill.tags:
            tag_words = set(re.findall(r"[\w]+", tag.lower()))
            if tag_words & keywords:
                score += 0.8

        sim = SequenceMatcher(None, task_description.lower(), skill.trigger.lower()).ratio()
        score += sim * 2.0

        success_bonus = skill.success_rate * 0.5
        score += success_bonus

        return score

    def get_skill_content(self, skill_id: str) -> str | None:
        """获取技能的 Markdown 内容。"""
        skill = self._skill_store.load_skill(skill_id)
        if not skill:
            return None
        self._skill_store.record_usage(skill_id)
        return skill.to_markdown()

    # ── CRUD 操作 ─────────────────────────────────────────

    def list_skills(
        self,
        status: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出技能。"""
        skills = self._skill_store.list_skills(status=status, tag=tag)
        return [
            {
                "id": s.id,
                "name": s.name,
                "trigger": s.trigger,
                "description": s.description,
                "status": s.status,
                "confidence": s.confidence,
                "usage_count": s.usage_count,
                "success_rate": round(s.success_rate, 3),
                "tags": s.tags,
                "version": s.version,
                "created_at": s.created_at,
                "last_used_at": s.last_used_at,
            }
            for s in skills
        ]

    def get_skill_detail(self, skill_id: str) -> dict[str, Any] | None:
        """获取技能详情。"""
        skill = self._skill_store.load_skill(skill_id)
        if not skill:
            return None
        return {
            **skill.to_meta_dict(),
            "markdown_content": skill.to_markdown(),
        }

    def create_skill(
        self,
        name: str,
        trigger: str,
        description: str,
        steps: list[str] | None = None,
        tools_required: list[str] | None = None,
        tags: list[str] | None = None,
        source_task_type: str = "general",
        initial_status: str = "draft",
    ) -> dict[str, Any]:
        """创建新技能。"""
        skill_steps = []
        if steps:
            for i, instruction in enumerate(steps, 1):
                skill_steps.append(SkillStep(order=i, instruction=instruction))

        skill = self._skill_store.create_skill(
            name=name,
            trigger=trigger,
            description=description,
            steps=skill_steps,
            tools_required=tools_required or [],
            tags=tags or [],
            source_task_type=source_task_type,
        )
        if initial_status == "active":
            skill.status = "active"
            skill.reviewed_at = utc_now()
            self._skill_store.write_skill(skill)

        return {
            "ok": True,
            "skill_id": skill.id,
            "name": skill.name,
            "status": skill.status,
        }

    def update_skill(
        self,
        skill_id: str,
        name: str | None = None,
        trigger: str | None = None,
        description: str | None = None,
        steps: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """更新技能。"""
        skill = self._skill_store.load_skill(skill_id)
        if not skill:
            return {"ok": False, "error": "Skill not found"}
        if skill.manual_override:
            return {"ok": False, "error": "Cannot update manually overridden skill"}

        if name is not None:
            skill.name = name
        if trigger is not None:
            skill.trigger = trigger
        if description is not None:
            skill.description = description
        if steps is not None:
            skill.steps = [
                SkillStep(order=i, instruction=step)
                for i, step in enumerate(steps, 1)
            ]
        if tags is not None:
            skill.tags = tags

        skill.managed_by = "user"
        self._skill_store.write_skill(skill)
        return {"ok": True, "skill_id": skill.id}

    def delete_skill(self, skill_id: str) -> dict[str, Any]:
        """删除技能。"""
        deleted = self._skill_store.delete_skill(skill_id)
        return {"ok": deleted}

    def review_skill(
        self,
        skill_id: str,
        action: str,
    ) -> dict[str, Any]:
        """审核技能。"""
        skill = self._skill_store.load_skill(skill_id)
        if not skill:
            return {"ok": False, "error": "Skill not found"}

        if action == "promote":
            if skill.status != "draft":
                return {"ok": False, "error": f"Cannot promote skill with status {skill.status}"}
            self._skill_store.promote_skill(skill_id)
            return {"ok": True, "action": "promoted"}
        elif action == "archive":
            self._skill_store.archive_skill(skill_id)
            return {"ok": True, "action": "archived"}
        elif action == "demote":
            skill = self._skill_store.load_skill(skill_id)
            if skill and skill.status == "active":
                skill.status = "draft"
                self._skill_store.write_skill(skill)
                return {"ok": True, "action": "demoted"}
            return {"ok": False, "error": f"Cannot demote skill with status {skill.status}"}
        return {"ok": False, "error": f"Unknown action: {action}"}

    # ── 反馈处理 ─────────────────────────────────────────

    def submit_feedback(
        self,
        skill_id: str,
        success: bool,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """提交技能使用反馈。"""
        skill = self._skill_store.load_skill(skill_id)
        if not skill:
            return {"ok": False, "error": "Skill not found"}

        self._skill_store.record_feedback(skill_id, success, notes)
        skill = self._skill_store.load_skill(skill_id)

        needs_review = (
            skill.usage_count >= 5 and
            skill.success_rate < 0.6
        ) if skill.usage_count >= 5 else False

        return {
            "ok": True,
            "skill_id": skill_id,
            "success": success,
            "new_usage_count": skill.usage_count,
            "new_success_count": skill.success_count,
            "new_failure_count": skill.failure_count,
            "success_rate": round(skill.success_rate, 3),
            "needs_improvement": needs_review,
        }

    # ── 技能改进 ─────────────────────────────────────────

    def improve_skill(
        self,
        skill_id: str,
        new_steps: list[str] | None = None,
        notes: str | None = None,
        auto: bool = False,
    ) -> dict[str, Any]:
        """改进技能（手动或基于反馈）。

        Args:
            skill_id: 技能 ID
            new_steps: 新的步骤列表（手动提供）
            notes: 改进说明
            auto: 是否尝试 LLM 自动改进（当 new_steps 和 notes 都为空时）
        """
        skill = self._skill_store.load_skill(skill_id)
        if not skill:
            return {"ok": False, "error": "Skill not found"}

        if new_steps:
            skill.steps = [
                SkillStep(order=i, instruction=step)
                for i, step in enumerate(new_steps, 1)
            ]
        elif notes:
            improved_steps = self._generate_improved_steps(skill, notes)
            if improved_steps:
                skill.steps = improved_steps
        elif auto:
            llm_improved = self._llm_improve_skill(skill)
            if llm_improved:
                skill.steps = llm_improved
                notes = "LLM 自动改进"

        skill.version += 1
        skill.improvement_count += 1
        skill.last_improved_at = utc_now()
        skill.managed_by = "user"
        skill.manual_override = True

        self._skill_store.write_skill(skill)

        return {
            "ok": True,
            "skill_id": skill_id,
            "new_version": skill.version,
            "improvement_count": skill.improvement_count,
        }

    def _generate_improved_steps(
        self,
        skill: Skill,
        feedback_notes: str,
    ) -> list[SkillStep]:
        """基于反馈生成改进的步骤。"""
        improved = []
        for i, step in enumerate(skill.steps, 1):
            improved.append(step)

        improved.append(SkillStep(
            order=len(improved) + 1,
            instruction=f"[改进] 根据反馈补充: {feedback_notes[:100]}",
        ))
        return improved

    def _llm_improve_skill(self, skill: Skill) -> list[SkillStep] | None:
        """尝试使用 LLM 自动改进技能。"""
        if not self.config.llm.enabled or not self.config.llm.is_complete():
            return None

        try:
            import openai
        except ImportError:
            return None

        failure_context = ""
        for fb in skill.feedback_history[-10:]:
            if not fb.get("success"):
                failure_context += f"- 失败: {fb.get('notes', '无备注')}\n"

        prompt = f"""你是一个技能改进专家。请分析以下技能的失败历史，提出改进建议。

## 技能信息
- 名称: {skill.name}
- 触发: {skill.trigger}
- 描述: {skill.description}

## 当前步骤
"""
        for step in skill.steps:
            prompt += f"{step.order}. {step.instruction}\n"

        prompt += f"""
## 失败历史（最近 10 条）
{failure_context or "无失败记录"}

请以 JSON 格式返回改进后的步骤列表：
{{"steps": ["步骤1描述", "步骤2描述", ...]}}

只返回 JSON，不要有其他内容。
"""
        client = openai.OpenAI(
            api_key=self.config.llm.api_key,
            base_url=self.config.llm.base_url,
            timeout=60,
        )
        try:
            resp = client.chat.completions.create(
                model=self.config.llm.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=500,
            )
            import json as _json
            content = resp.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = _json.loads(content)
            steps = [
                SkillStep(order=i + 1, instruction=s)
                for i, s in enumerate(data.get("steps", []))
            ]
            self.logger.info(f"LLM improved skill {skill.id}: {len(steps)} new steps")
            return steps
        except Exception as e:
            self.logger.warning(f"LLM skill improvement failed: {e}")
            return None

    def get_skills_for_review(self) -> list[dict[str, Any]]:
        """获取需要审核的技能列表。"""
        draft_skills = self._skill_store.list_skills(status="draft")
        needs_improvement = self._skill_store.get_skills_needing_improvement()

        return {
            "draft": [
                {
                    "id": s.id,
                    "name": s.name,
                    "trigger": s.trigger,
                    "description": s.description,
                    "created_at": s.created_at,
                }
                for s in draft_skills
            ],
            "needs_improvement": [
                {
                    "id": s.id,
                    "name": s.name,
                    "success_rate": round(s.success_rate, 3),
                    "usage_count": s.usage_count,
                    "failure_count": s.failure_count,
                    "last_failure_at": s.last_failure_at,
                }
                for s in needs_improvement
            ],
        }

    # ── 统计 ─────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取技能统计信息。"""
        return self._skill_store.get_skill_stats()

    # ── 从 Dream/Memory 抽取 ────────────────────────────────

    def extract_skill_from_dream(
        self,
        dream_id: str,
        skill_name: str,
        skill_trigger: str,
    ) -> dict[str, Any]:
        """从 Dream 中抽取技能。"""
        dream = self._dream_store.load_dream(dream_id)
        if not dream:
            return {"ok": False, "error": "Dream not found"}

        steps = []
        if dream.key_decisions:
            for decision in dream.key_decisions:
                steps.append(SkillStep(
                    order=len(steps) + 1,
                    instruction=f"关键决策: {decision[:150]}",
                ))

        for spark in dream.sparks:
            if spark.code_snippet:
                steps.append(SkillStep(
                    order=len(steps) + 1,
                    instruction=f"使用代码模式: {spark.content[:100]}",
                    tools_needed=[spark.language] if spark.language else [],
                ))

        skill = self._skill_store.create_skill(
            name=skill_name,
            trigger=skill_trigger,
            description=f"从 Dream '{dream.title}' 抽取的技能",
            steps=steps,
            tools_required=list({s.language for s in dream.sparks if s.language}),
            tags=dream.tags + [dream.category],
            source_task_type=dream.category,
        )
        skill.source_dream_ids = [dream_id]
        self._skill_store.write_skill(skill)

        return {
            "ok": True,
            "skill_id": skill.id,
            "name": skill.name,
            "steps_count": len(steps),
        }


# ── 便捷函数 ─────────────────────────────────────────────

def get_skill_service(config: MemoryConfig | None = None) -> SkillService:
    """获取技能服务实例。"""
    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()
    return SkillService(config)
