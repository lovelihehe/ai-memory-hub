"""
用户画像（UserProfile）数据模型。

对应 Hermes L4 Honcho 层，通过正-反-合辩证循环持续进化用户画像。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ai_memory_hub.core.models import utc_now


@dataclass(slots=True)
class DialecticEntry:
    """辩证记录：正-反-合的每一次循环。"""
    cycle: int                         # 循环次数
    thesis: str                        # 正（现有假设）
    antithesis: str                     # 反（新信息）
    synthesis: str                      # 合（调和结果）
    trigger_event: str                  # 触发辩证的事件摘要
    timestamp: str                       # 时间戳


@dataclass(slots=True)
class GrowthEntry:
    """成长记录：用户能力和经验的演进。"""
    timestamp: str                      # 时间戳
    milestone: str                       # 里程碑描述
    evidence: str                        # 证据/来源
    skill_added: str | None = None      # 新增技能


@dataclass(slots=True)
class UserProfile:
    """
    UserProfile（用户画像）。

    通过正-反-合辩证循环持续进化，反映用户的：
    - 角色与专业领域
    - 工作风格与沟通偏好
    - 工具使用习惯
    - 项目经历与成长轨迹
    """
    # ── 基础信息 ─────────────────────────────────────────
    id: str = "default"                  # 固定为 "default"
    role: str = ""                       # 角色（工程师/设计师/管理者）
    expertise: list[str] = field(default_factory=list)  # 专业领域
    working_style: str = ""              # 工作风格

    # ── 偏好 ─────────────────────────────────────────────
    communication_style: str = ""         # 沟通偏好
    tool_preferences: dict[str, int] = field(default_factory=dict)  # 工具偏好及使用频率
    language_preference: str = ""        # 语言偏好（中文/英文）
    output_format_preference: str = ""    # 输出格式偏好（Markdown/JSON/纯文本）

    # ── 经验追踪 ─────────────────────────────────────────
    project_history: list[str] = field(default_factory=list)  # 项目经历
    technology_stack: list[str] = field(default_factory=list)  # 技术栈
    domain_knowledge: list[str] = field(default_factory=list)  # 领域知识

    # ── 辩证进化 ─────────────────────────────────────────
    dialectic_cycles: int = 0            # 辩证循环次数
    dialectic_history: list[dict] = field(default_factory=list)  # 辩证历史
    last_dialectic_at: str | None = None  # 上次辩证更新时间

    # ── 成长追踪 ─────────────────────────────────────────
    growth_trajectory: list[dict] = field(default_factory=list)  # 成长轨迹
    total_sessions: int = 0              # 累计会话数
    total_tasks_completed: int = 0       # 累计完成任务数
    total_skills_created: int = 0       # 累计创建技能数

    # ── 质量指标 ─────────────────────────────────────────
    profile_confidence: float = 0.5      # 画像置信度 [0.0, 1.0]
    last_updated: str = field(default_factory=utc_now)
    last_context_used: str | None = None  # 最后 context 使用时间

    # ── 版本控制 ─────────────────────────────────────────
    version: int = 1                     # 画像版本

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return asdict(self)

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "UserProfile":
        """从字典反序列化。"""
        if not isinstance(payload, dict):
            raise TypeError(f"UserProfile.from_dict expects a dict, got {type(payload).__name__}")
        return UserProfile(
            id=payload.get("id", "default"),
            role=payload.get("role", ""),
            expertise=list(payload.get("expertise", [])),
            working_style=payload.get("working_style", ""),
            communication_style=payload.get("communication_style", ""),
            tool_preferences=dict(payload.get("tool_preferences", {})),
            language_preference=payload.get("language_preference", ""),
            output_format_preference=payload.get("output_format_preference", ""),
            project_history=list(payload.get("project_history", [])),
            technology_stack=list(payload.get("technology_stack", [])),
            domain_knowledge=list(payload.get("domain_knowledge", [])),
            dialectic_cycles=int(payload.get("dialectic_cycles", 0)),
            dialectic_history=list(payload.get("dialectic_history", [])),
            last_dialectic_at=payload.get("last_dialectic_at"),
            growth_trajectory=list(payload.get("growth_trajectory", [])),
            total_sessions=int(payload.get("total_sessions", 0)),
            total_tasks_completed=int(payload.get("total_tasks_completed", 0)),
            total_skills_created=int(payload.get("total_skills_created", 0)),
            profile_confidence=float(payload.get("profile_confidence", 0.5)),
            last_updated=payload.get("last_updated", utc_now()),
            last_context_used=payload.get("last_context_used"),
            version=int(payload.get("version", 1)),
        )

    def add_dialectic(
        self,
        thesis: str,
        antithesis: str,
        synthesis: str,
        trigger_event: str,
    ) -> DialecticEntry:
        """
        添加一次辩证循环记录。
        """
        self.dialectic_cycles += 1
        entry = DialecticEntry(
            cycle=self.dialectic_cycles,
            thesis=thesis,
            antithesis=antithesis,
            synthesis=synthesis,
            trigger_event=trigger_event,
            timestamp=utc_now(),
        )
        self.dialectic_history.append(asdict(entry))
        self.last_dialectic_at = utc_now()

        # 置信度根据辩证结果调整
        if len(synthesis) > len(thesis):
            self.profile_confidence = min(0.99, self.profile_confidence + 0.05)
        else:
            self.profile_confidence = max(0.1, self.profile_confidence - 0.02)

        self.version += 1
        return entry

    def add_growth_entry(
        self,
        milestone: str,
        evidence: str,
        skill_added: str | None = None,
    ) -> GrowthEntry:
        """添加成长记录。"""
        entry = GrowthEntry(
            timestamp=utc_now(),
            milestone=milestone,
            evidence=evidence,
            skill_added=skill_added,
        )
        self.growth_trajectory.append(asdict(entry))
        if skill_added:
            self.total_skills_created += 1
        return entry

    def record_tool_usage(self, tool_id: str) -> None:
        """记录工具使用。"""
        if tool_id not in self.tool_preferences:
            self.tool_preferences[tool_id] = 0
        self.tool_preferences[tool_id] += 1

    def add_expertise(self, expertise: str) -> None:
        """添加专业领域。"""
        if expertise not in self.expertise:
            self.expertise.append(expertise)

    def add_project(self, project_key: str) -> None:
        """添加项目经历。"""
        if project_key not in self.project_history:
            self.project_history.append(project_key)

    def add_technology(self, tech: str) -> None:
        """添加技术栈。"""
        if tech not in self.technology_stack:
            self.technology_stack.append(tech)

    def to_markdown(self) -> str:
        """渲染为 Markdown 格式。"""
        lines = [
            "# 用户画像",
            "",
            f"**版本**: {self.version} | **置信度**: {self.profile_confidence:.0%}",
            f"**最后更新**: {self.last_updated}",
            f"**辩证循环次数**: {self.dialectic_cycles}",
            "",
        ]

        if self.role:
            lines.append(f"**角色**: {self.role}")
        if self.expertise:
            lines.append(f"**专业领域**: {', '.join(self.expertise)}")
        if self.working_style:
            lines.append(f"**工作风格**: {self.working_style}")
        if self.communication_style:
            lines.append(f"**沟通偏好**: {self.communication_style}")
        if self.language_preference:
            lines.append(f"**语言偏好**: {self.language_preference}")
        if self.output_format_preference:
            lines.append(f"**输出格式**: {self.output_format_preference}")
        lines.append("")

        if self.technology_stack:
            lines.append("## 技术栈")
            for tech in self.technology_stack:
                lines.append(f"- {tech}")
            lines.append("")

        if self.tool_preferences:
            lines.append("## 工具使用频率")
            sorted_tools = sorted(self.tool_preferences.items(), key=lambda x: -x[1])
            for tool, count in sorted_tools:
                lines.append(f"- {tool}: {count} 次")
            lines.append("")

        if self.project_history:
            lines.append(f"## 项目经历 ({len(self.project_history)} 个)")
            for proj in self.project_history[-10:]:
                lines.append(f"- {proj}")
            lines.append("")

        if self.growth_trajectory:
            lines.append("## 成长轨迹")
            for entry in self.growth_trajectory[-10:]:
                lines.append(f"- [{entry['timestamp'][:10]}] {entry['milestone']}")
                if entry.get("skill_added"):
                    lines.append(f"  - 新增技能: {entry['skill_added']}")
            lines.append("")

        if self.dialectic_history:
            lines.append(f"## 辩证进化历史 ({self.dialectic_cycles} 次)")
            for entry in self.dialectic_history[-5:]:
                lines.append(f"- 循环 {entry['cycle']}: {entry['synthesis'][:80]}...")
            lines.append("")

        lines.append("---")
        lines.append(f"*累计会话: {self.total_sessions} | 完成任务: {self.total_tasks_completed} | 创建技能: {self.total_skills_created}*")

        return "\n".join(lines) + "\n"
