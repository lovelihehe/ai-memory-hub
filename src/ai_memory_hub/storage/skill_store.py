"""
技能存储层。

负责 Skill 的持久化存储，提供：
- Skill CRUD 操作
- 技能索引管理
- 向量存储集成（复用 VectorStore）
- 与 Dream/Memory 的关联管理
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.core.skill_models import Skill, SkillExample, SkillStep
from ai_memory_hub.core.utils import ensure_parent


class SkillStore:
    """技能存储引擎。"""

    SKILL_DIR = "skills"
    INDEX_FILE = "index.json"
    _INDEX_FLUSH_THRESHOLD = 50

    def __init__(self, config: MemoryConfig):
        self.config = config
        self.root = config.data_home_path
        self.skills_root = self.root / self.SKILL_DIR
        self.index_path = self.skills_root / self.INDEX_FILE
        self.logger = get_logger(self.root / "logs")
        self._index_cache: dict[str, dict[str, Any]] | None = None
        self._index_dirty = False
        self._pending_updates = 0

    def ensure_layout(self) -> None:
        """确保目录结构存在。"""
        self.skills_root.mkdir(parents=True, exist_ok=True)

    def _skill_meta_path(self, skill_id: str) -> Path:
        """获取技能元数据文件路径。"""
        return self.skills_root / f"{skill_id}.meta.json"

    def _skill_md_path(self, skill_id: str) -> Path:
        """获取技能 Markdown 文件路径。"""
        return self.skills_root / f"{skill_id}.md"

    def new_skill_id(self) -> str:
        """生成新的技能 ID。"""
        return f"skill-{uuid.uuid4().hex[:12]}"

    def write_skill(self, skill: Skill) -> Path:
        """写入技能到文件系统。"""
        self.ensure_layout()

        # 写入 Markdown 文件（AI 可读）
        md_path = self._skill_md_path(skill.id)
        ensure_parent(md_path)
        md_path.write_text(skill.to_markdown(), encoding="utf-8")

        # 写入元数据 JSON
        meta_path = self._skill_meta_path(skill.id)
        ensure_parent(meta_path)
        meta_path.write_text(
            json.dumps(skill.to_meta_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.logger.info(
            f"Written skill: {skill.id} ({skill.name}) - "
            f"status={skill.status}, version={skill.version}"
        )

        self._update_index(skill)
        return meta_path

    def load_skill(self, skill_id: str) -> Skill | None:
        """按 ID 加载技能。"""
        meta_path = self._skill_meta_path(skill_id)
        if not meta_path.exists():
            return None
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            return Skill.from_dict(payload)
        except Exception as e:
            self.logger.warning(f"Failed to load skill {skill_id}: {e}")
            return None

    def delete_skill(self, skill_id: str) -> bool:
        """删除技能。"""
        md_path = self._skill_md_path(skill_id)
        meta_path = self._skill_meta_path(skill_id)
        deleted = False
        if md_path.exists():
            md_path.unlink()
            deleted = True
        if meta_path.exists():
            meta_path.unlink()
            deleted = True
        self._remove_from_index(skill_id)
        return deleted

    def list_skills(
        self,
        status: str | None = None,
        tag: str | None = None,
        include_archived: bool = False,
    ) -> list[Skill]:
        """列出技能，支持按状态和标签过滤。"""
        skills: list[Skill] = []
        if not self.skills_root.exists():
            return skills
        for path in sorted(self.skills_root.glob("*.meta.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                skill = Skill.from_dict(payload)
                if status and status != "all" and skill.status != status:
                    continue
                if tag and tag not in skill.tags:
                    continue
                if skill.status == "archived" and not include_archived:
                    continue
                skills.append(skill)
            except Exception:
                continue
        return sorted(skills, key=lambda s: s.created_at, reverse=True)

    def search_skills(self, query: str, limit: int = 10) -> list[Skill]:
        """在技能中进行关键词搜索。"""
        skills = self.list_skills(status="active")
        if not query.strip():
            return skills[:limit]
        query_lower = query.lower()
        scored: list[tuple[float, Skill]] = []
        for skill in skills:
            score = 0.0
            if query_lower in skill.name.lower():
                score += 4.0
            if query_lower in skill.trigger.lower():
                score += 3.0
            if query_lower in skill.description.lower():
                score += 2.0
            for tag in skill.tags:
                if query_lower in tag.lower():
                    score += 1.5
            for step in skill.steps:
                if query_lower in step.instruction.lower():
                    score += 1.0
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda item: -item[0])
        return [skill for _, skill in scored[:limit]]

    def record_usage(self, skill_id: str) -> None:
        """记录技能被使用。"""
        skill = self.load_skill(skill_id)
        if not skill:
            return
        skill.usage_count += 1
        skill.last_used_at = skill.created_at.split("T")[0]  # simplified
        from ai_memory_hub.core.models import utc_now
        skill.last_used_at = utc_now()
        self.write_skill(skill)

    def record_feedback(
        self,
        skill_id: str,
        success: bool,
        notes: str | None = None,
    ) -> None:
        """记录技能使用反馈。"""
        skill = self.load_skill(skill_id)
        if not skill:
            return
        from ai_memory_hub.core.models import utc_now
        now = utc_now()
        if success:
            skill.success_count += 1
            skill.last_success_at = now
        else:
            skill.failure_count += 1
            skill.last_failure_at = now
        skill.usage_count += 1
        skill.last_used_at = now
        skill.feedback_history.append({
            "timestamp": now,
            "success": success,
            "notes": notes,
        })
        self.write_skill(skill)

    def archive_skill(self, skill_id: str) -> bool:
        """归档技能。"""
        skill = self.load_skill(skill_id)
        if not skill:
            return False
        skill.status = "archived"
        self.write_skill(skill)
        return True

    def promote_skill(self, skill_id: str) -> bool:
        """将技能从 draft 提升为 active。"""
        skill = self.load_skill(skill_id)
        if not skill:
            return False
        if skill.status != "draft":
            return False
        skill.status = "active"
        from ai_memory_hub.core.models import utc_now
        skill.reviewed_at = utc_now()
        self.write_skill(skill)
        return True

    # ── 索引管理 ────────────────────────────────────────────

    def _ensure_index_cache(self) -> None:
        """确保索引缓存已加载。"""
        if self._index_cache is None:
            self._index_cache = {}
            if self.index_path.exists():
                try:
                    self._index_cache = json.loads(self.index_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

    def _persist_index(self) -> None:
        """将索引缓存写入磁盘。"""
        if self._index_cache is None or not self._index_dirty:
            return
        try:
            self.index_path.write_text(
                json.dumps(self._index_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._index_dirty = False
        except Exception as e:
            self.logger.warning(f"Failed to persist skill index: {e}")

    def _update_index(self, skill: Skill, immediate: bool = False) -> None:
        """更新技能索引文件。"""
        self._ensure_index_cache()
        if self._index_cache is None:
            return
        self._index_cache[skill.id] = {
            "name": skill.name,
            "trigger": skill.trigger,
            "tags": skill.tags,
            "status": skill.status,
            "version": skill.version,
            "confidence": skill.confidence,
            "usage_count": skill.usage_count,
            "success_rate": skill.success_rate,
            "created_at": skill.created_at,
        }
        self._index_dirty = True
        self._pending_updates += 1
        if immediate or self._pending_updates >= self._INDEX_FLUSH_THRESHOLD:
            self._persist_index()
            self._pending_updates = 0

    def _remove_from_index(self, skill_id: str) -> None:
        """从索引中移除技能。"""
        self._ensure_index_cache()
        if self._index_cache is None or not self.index_path.exists():
            return
        try:
            self._index_cache.pop(skill_id, None)
            self._index_dirty = True
            self._persist_index()
        except Exception as e:
            self.logger.warning(f"Failed to remove skill from index: {e}")

    def flush_index(self) -> None:
        """强制将内存中的索引写入磁盘。"""
        self._persist_index()
        self._pending_updates = 0

    def get_index(self) -> dict[str, dict[str, Any]]:
        """获取技能索引。"""
        self._ensure_index_cache()
        return self._index_cache if self._index_cache is not None else {}

    # ── 统计 ────────────────────────────────────────────────

    def count_skills(self, status: str | None = None) -> int:
        """统计技能数量。"""
        return len(self.list_skills(status=status))

    def get_skill_stats(self) -> dict[str, Any]:
        """获取技能统计信息。"""
        skills = self.list_skills(include_archived=True)
        active = [s for s in skills if s.status == "active"]
        draft = [s for s in skills if s.status == "draft"]
        archived = [s for s in skills if s.status == "archived"]
        total_usage = sum(s.usage_count for s in skills)
        total_success = sum(s.success_count for s in skills)
        total_failure = sum(s.failure_count for s in skills)

        tag_counts: dict[str, int] = {}
        for skill in active:
            for tag in skill.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        return {
            "total": len(skills),
            "active": len(active),
            "draft": len(draft),
            "archived": len(archived),
            "total_usage": total_usage,
            "total_success": total_success,
            "total_failure": total_failure,
            "overall_success_rate": total_success / total_usage if total_usage > 0 else 0.0,
            "tag_counts": tag_counts,
            "needs_review": len(draft),
        }

    def get_skills_needing_improvement(self) -> list[Skill]:
        """获取需要改进的技能（成功率低于阈值）。"""
        skills = self.list_skills(status="active")
        threshold = 0.6
        return [
            s for s in skills
            if s.usage_count >= 5 and s.success_rate < threshold
        ]

    # ── 创建辅助 ───────────────────────────────────────────

    def create_skill(
        self,
        name: str,
        trigger: str,
        description: str,
        steps: list[SkillStep] | None = None,
        examples: list[SkillExample] | None = None,
        tools_required: list[str] | None = None,
        tags: list[str] | None = None,
        source_task_type: str = "general",
        initial_confidence: float = 0.6,
    ) -> Skill:
        """创建新技能。"""
        skill = Skill(
            id=self.new_skill_id(),
            name=name,
            trigger=trigger,
            description=description,
            steps=steps or [],
            examples=examples or [],
            tools_required=tools_required or [],
            tags=tags or [],
            source_dream_ids=[],
            source_memory_ids=[],
            source_task_type=source_task_type,
            confidence=initial_confidence,
            usage_count=0,
            success_count=0,
            failure_count=0,
            last_success_at=None,
            last_failure_at=None,
            status="draft",
        )
        self.write_skill(skill)
        return skill
