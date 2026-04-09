"""
用户画像服务。

提供用户画像的加载、保存和辩证进化功能。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.core.models import utc_now
from ai_memory_hub.core.profile_models import UserProfile


class ProfileService:
    """用户画像服务。"""

    PROFILE_FILE = "profile.json"

    def __init__(self, config: MemoryConfig):
        self.config = config
        self.profile_path = config.data_home_path / self.PROFILE_FILE
        self.logger = get_logger(config.data_home_path / "logs")
        self._profile: UserProfile | None = None

    def _ensure_profile(self) -> UserProfile:
        """确保画像已加载。"""
        if self._profile is None:
            self._profile = self._load_profile()
        return self._profile

    def _load_profile(self) -> UserProfile:
        """从磁盘加载画像。"""
        if not self.profile_path.exists():
            profile = UserProfile()
            self._save_profile(profile)
            return profile
        try:
            payload = json.loads(self.profile_path.read_text(encoding="utf-8"))
            return UserProfile.from_dict(payload)
        except Exception as e:
            self.logger.warning(f"Failed to load profile: {e}, creating new one")
            return UserProfile()

    def _save_profile(self, profile: UserProfile) -> None:
        """保存画像到磁盘。"""
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_profile(self) -> dict[str, Any]:
        """获取用户画像。"""
        profile = self._ensure_profile()
        return {
            **profile.to_dict(),
            "markdown": profile.to_markdown(),
        }

    def update_profile(
        self,
        role: str | None = None,
        expertise: list[str] | None = None,
        working_style: str | None = None,
        communication_style: str | None = None,
        language_preference: str | None = None,
        output_format_preference: str | None = None,
    ) -> dict[str, Any]:
        """更新用户画像基础信息。"""
        profile = self._ensure_profile()
        if role is not None:
            profile.role = role
        if expertise is not None:
            profile.expertise = expertise
        if working_style is not None:
            profile.working_style = working_style
        if communication_style is not None:
            profile.communication_style = communication_style
        if language_preference is not None:
            profile.language_preference = language_preference
        if output_format_preference is not None:
            profile.output_format_preference = output_format_preference
        profile.last_updated = utc_now()
        self._save_profile(profile)
        return {"ok": True, "updated": True}

    def evolve_profile(
        self,
        thesis: str | None = None,
        antithesis: str | None = None,
        synthesis: str | None = None,
        trigger_event: str | None = None,
    ) -> dict[str, Any]:
        """
        执行一次辩证循环来进化用户画像。

        如果未提供参数，自动从现有记忆和技能中推断辩证内容。
        """
        profile = self._ensure_profile()

        if thesis and antithesis and synthesis:
            entry = profile.add_dialectic(
                thesis=thesis,
                antithesis=antithesis,
                synthesis=synthesis,
                trigger_event=trigger_event or "manual",
            )
            profile.last_updated = utc_now()
            self._save_profile(profile)
            return {
                "ok": True,
                "cycle": entry.cycle,
                "thesis": entry.thesis,
                "antithesis": entry.antithesis,
                "synthesis": entry.synthesis,
                "total_cycles": profile.dialectic_cycles,
            }

        inferred = self._infer_dialectic()
        if inferred:
            entry = profile.add_dialectic(
                thesis=inferred["thesis"],
                antithesis=inferred["antithesis"],
                synthesis=inferred["synthesis"],
                trigger_event=inferred["trigger"],
            )
            profile.last_updated = utc_now()
            self._save_profile(profile)
            return {
                "ok": True,
                "cycle": entry.cycle,
                "thesis": entry.thesis,
                "antithesis": entry.antithesis,
                "synthesis": entry.synthesis,
                "total_cycles": profile.dialectic_cycles,
                "inferred": True,
            }

        return {
            "ok": False,
            "error": "No dialectic material available",
        }

    def _infer_dialectic(self) -> dict[str, str] | None:
        """
        从现有记忆和技能中推断辩证内容。

        分析最近的记忆变化和技能使用情况，识别画像中的矛盾或需要进化的地方。
        """
        from ai_memory_hub.services.manage import list_memories
        from ai_memory_hub.storage.db import MemoryStore
        from ai_memory_hub.storage.skill_store import SkillStore

        store = MemoryStore(self.config)
        skill_store = SkillStore(self.config)
        profile = self._ensure_profile()

        memories = list_memories(store, status="active", limit=20)
        skills = skill_store.list_skills(status="active")

        if not memories and not skills:
            return None

        thesis_parts = []
        antithesis_parts = []

        if profile.expertise:
            thesis_parts.append(f"现有专业领域: {', '.join(profile.expertise[:3])}")
        if profile.technology_stack:
            thesis_parts.append(f"技术栈: {', '.join(profile.technology_stack[:3])}")

        new_expertise = set()
        new_tools = set()
        for mem in memories:
            for tag in mem.get("tags", []):
                if tag not in profile.expertise and len(tag) > 2:
                    new_expertise.add(tag)
        for skill in skills:
            for tool in skill.tools_required:
                if tool not in profile.tool_preferences:
                    new_tools.add(tool)

        if new_expertise:
            antithesis_parts.append(f"新发现领域: {', '.join(list(new_expertise)[:5])}")
        if new_tools:
            antithesis_parts.append(f"新使用工具: {', '.join(list(new_tools)[:5])}")

        low_success_skills = [s for s in skills if s.usage_count >= 3 and s.success_rate < 0.5]
        if low_success_skills:
            antithesis_parts.append(f"成功率低的技能: {', '.join([s.name for s in low_success_skills[:3]])}")

        if not antithesis_parts:
            return None

        thesis = " | ".join(thesis_parts) if thesis_parts else "现有画像未记录显著偏好"
        antithesis = " | ".join(antithesis_parts)
        synthesis = self._generate_synthesis(
            thesis, antithesis, list(new_expertise), list(new_tools), low_success_skills
        )

        return {
            "thesis": thesis,
            "antithesis": antithesis,
            "synthesis": synthesis,
            "trigger": "auto-inferred from memories and skills",
        }

    def _generate_synthesis(
        self,
        thesis: str,
        antithesis: str,
        new_expertise: list[str],
        new_tools: list[str],
        low_success_skills: list,
    ) -> str:
        """生成调和结果。"""
        synthesis_parts = []

        if new_expertise:
            synthesis_parts.append(f"扩展专业领域: 新增 {', '.join(new_expertise[:3])}")
        if new_tools:
            synthesis_parts.append(f"工具偏好进化: 尝试使用 {', '.join(new_tools[:3])}")
        if low_success_skills:
            synthesis_parts.append(f"技能优化: 改进 {', '.join([s.name for s in low_success_skills[:2]])} 的执行策略")

        if synthesis_parts:
            return " ; ".join(synthesis_parts)

        recent_cycles = self._profile.dialectic_history[-3:] if self._profile.dialectic_history else []
        if recent_cycles:
            last_synthesis = recent_cycles[-1].get("synthesis", "")
            return f"延续之前的方向: {last_synthesis[:60]}"

        return "画像保持稳定，无显著变化需要调和"

    def add_growth_entry(
        self,
        milestone: str,
        evidence: str,
        skill_added: str | None = None,
    ) -> dict[str, Any]:
        """添加成长记录。"""
        profile = self._ensure_profile()
        entry = profile.add_growth_entry(
            milestone=milestone,
            evidence=evidence,
            skill_added=skill_added,
        )
        profile.last_updated = utc_now()
        self._save_profile(profile)
        return {"ok": True, "entry": asdict(entry)}

    def record_session(self) -> None:
        """记录一次会话。"""
        profile = self._ensure_profile()
        profile.total_sessions += 1
        profile.last_updated = utc_now()
        self._save_profile(profile)

    def record_task_completed(self) -> None:
        """记录一次任务完成。"""
        profile = self._ensure_profile()
        profile.total_tasks_completed += 1
        profile.last_updated = utc_now()
        self._save_profile(profile)

    def record_context_use(self) -> None:
        """记录一次 context 调用。"""
        profile = self._ensure_profile()
        profile.last_context_used = utc_now()
        profile.last_updated = utc_now()
        self._save_profile(profile)

    def record_tool_usage(self, tool_id: str) -> None:
        """记录工具使用。"""
        profile = self._ensure_profile()
        profile.record_tool_usage(tool_id)
        profile.last_updated = utc_now()
        self._save_profile(profile)

    def add_skill_created(self, skill_name: str) -> None:
        """记录创建的技能。"""
        profile = self._ensure_profile()
        profile.total_skills_created += 1
        profile.add_growth_entry(
            milestone=f"创建新技能: {skill_name}",
            evidence="skill creation",
            skill_added=skill_name,
        )
        profile.last_updated = utc_now()
        self._save_profile(profile)


def get_profile_service(config: MemoryConfig | None = None) -> ProfileService:
    """获取用户画像服务实例。"""
    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()
    return ProfileService(config)
