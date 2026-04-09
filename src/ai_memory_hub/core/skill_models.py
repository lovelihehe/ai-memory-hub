"""
技能（Skill）数据模型。

Skill 是可复用的程序性知识单元，对应 Hermes L2 SKILL.md 层。
每个 Skill 代表一个可独立执行的技能，支持：
- CRUD 操作
- 使用反馈追踪
- 自我改进循环
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ai_memory_hub.core.models import utc_now


@dataclass(slots=True)
class SkillStep:
    """技能执行步骤。"""
    order: int                       # 步骤序号
    instruction: str                 # 步骤指令
    expected_output: str | None = None  # 期望输出
    tools_needed: list[str] = field(default_factory=list)  # 所需工具


@dataclass(slots=True)
class SkillExample:
    """技能使用示例。"""
    description: str                 # 示例描述
    input_text: str | None = None    # 输入示例
    expected_result: str | None = None  # 期望结果
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkillFeedback:
    """技能使用反馈记录。"""
    timestamp: str                    # 反馈时间
    success: bool                    # 是否成功
    notes: str | None = None         # 用户备注
    task_description: str | None = None  # 任务描述


@dataclass(slots=True)
class Skill:
    """
    Skill（技能）：可复用的程序性知识单元。

    生命周期：draft → active → (archived | improved)

    核心设计：
    - 存储为 Markdown 格式（skills/{id}.md），便于 AI 直接读取执行
    - 元数据存储为 JSON（skills/{id}.meta.json），供系统管理
    - 技能以 Markdown 开头，方便 AI 直接理解和使用
    """
    # ── 标识 ──────────────────────────────────────────────
    id: str                           # "skill-{uuid}"，唯一标识
    name: str                         # 技能名称（如 "api-endpoint-builder"）
    trigger: str                      # 触发条件（自然语言描述的任务类型）
    description: str                   # 技能描述（简短说明）

    # ── 内容 ──────────────────────────────────────────────
    steps: list[SkillStep]            # 执行步骤（有序列表）
    examples: list[SkillExample]      # 使用示例
    tools_required: list[str]          # 所需工具（如 ["shell", "git", "python"]）
    tags: list[str]                   # 技能标签（如 ["backend", "api", "fastapi"]）

    # ── 来源追踪 ──────────────────────────────────────────
    source_dream_ids: list[str]      # 来源 Dream ID 列表
    source_memory_ids: list[str]       # 来源 MemoryRecord ID 列表
    source_task_type: str             # 来源任务类型（creation/debug/learning 等）

    # ── 质量指标 ──────────────────────────────────────────
    confidence: float                 # 置信度 [0.0, 1.0]
    usage_count: int                  # 累计使用次数
    success_count: int                # 成功次数
    failure_count: int                 # 失败次数
    last_success_at: str | None      # 上次成功时间
    last_failure_at: str | None      # 上次失败时间

    # ── 版本追踪 ──────────────────────────────────────────
    version: int = 1                  # 版本号，每次改进后 +1
    improvement_count: int = 0        # 改进次数
    created_at: str = field(default_factory=utc_now)
    last_used_at: str | None = None
    last_improved_at: str | None = None
    reviewed_at: str | None = None

    # ── 生命周期 ─────────────────────────────────────────
    status: str = "draft"            # draft / active / archived
    managed_by: str = "system"       # system / user / llm
    manual_override: bool = False     # 是否被用户手动修改

    # ── 反馈历史 ──────────────────────────────────────────
    feedback_history: list[dict] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """计算技能成功率。"""
        if self.usage_count == 0:
            return 0.0
        return self.success_count / self.usage_count

    @property
    def failure_rate(self) -> float:
        """计算技能失败率。"""
        if self.usage_count == 0:
            return 0.0
        return self.failure_count / self.usage_count

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        payload = asdict(self)
        payload["steps"] = [asdict(s) for s in self.steps]
        payload["examples"] = [asdict(e) for e in self.examples]
        return payload

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "Skill":
        """从字典反序列化。"""
        if not isinstance(payload, dict):
            raise TypeError(f"Skill.from_dict expects a dict, got {type(payload).__name__}")

        required_fields = ["id", "name", "trigger", "description"]
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise KeyError(f"Skill.from_dict missing required fields: {missing}")

        confidence = float(payload.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        steps = [
            SkillStep(**item) for item in payload.get("steps", [])
        ]
        examples = [
            SkillExample(**item) for item in payload.get("examples", [])
        ]

        return Skill(
            id=payload["id"],
            name=payload["name"],
            trigger=payload["trigger"],
            description=payload["description"],
            steps=steps,
            examples=examples,
            tools_required=list(payload.get("tools_required", [])),
            tags=list(payload.get("tags", [])),
            source_dream_ids=list(payload.get("source_dream_ids", [])),
            source_memory_ids=list(payload.get("source_memory_ids", [])),
            source_task_type=payload.get("source_task_type", "general"),
            confidence=confidence,
            usage_count=int(payload.get("usage_count", 0)),
            success_count=int(payload.get("success_count", 0)),
            failure_count=int(payload.get("failure_count", 0)),
            last_success_at=payload.get("last_success_at"),
            last_failure_at=payload.get("last_failure_at"),
            version=int(payload.get("version", 1)),
            improvement_count=int(payload.get("improvement_count", 0)),
            created_at=payload.get("created_at", utc_now()),
            last_used_at=payload.get("last_used_at"),
            last_improved_at=payload.get("last_improved_at"),
            reviewed_at=payload.get("reviewed_at"),
            status=payload.get("status", "draft"),
            managed_by=payload.get("managed_by", "system"),
            manual_override=bool(payload.get("manual_override", False)),
            feedback_history=list(payload.get("feedback_history", [])),
        )

    def to_markdown(self) -> str:
        """渲染为 AI 可读的 Markdown 格式。"""
        lines = [
            f"# {self.name}",
            "",
            f"**描述**: {self.description}",
            f"**触发条件**: {self.trigger}",
            "",
        ]

        if self.tags:
            lines.append(f"**标签**: {', '.join(self.tags)}")
            lines.append("")

        if self.tools_required:
            lines.append(f"**所需工具**: {', '.join(self.tools_required)}")
            lines.append("")

        if self.steps:
            lines.append("## 执行步骤")
            for step in self.steps:
                lines.append(f"{step.order}. {step.instruction}")
                if step.expected_output:
                    lines.append(f"   - 期望输出: {step.expected_output}")
                if step.tools_needed:
                    lines.append(f"   - 工具: {', '.join(step.tools_needed)}")
            lines.append("")

        if self.examples:
            lines.append("## 使用示例")
            for i, example in enumerate(self.examples, 1):
                lines.append(f"### 示例 {i}: {example.description}")
                if example.input_text:
                    lines.append(f"**输入**: {example.input_text}")
                if example.expected_result:
                    lines.append(f"**期望结果**: {example.expected_result}")
                lines.append("")

        lines.append(f"---\n*技能版本: {self.version} | 使用次数: {self.usage_count} | 成功率: {self.success_rate:.0%}*")

        return "\n".join(lines).rstrip() + "\n"

    def to_meta_dict(self) -> dict[str, Any]:
        """导出元数据字典（不含 Markdown 内容）。"""
        return {
            "id": self.id,
            "name": self.name,
            "trigger": self.trigger,
            "description": self.description,
            "steps": [asdict(s) for s in self.steps],
            "examples": [asdict(e) for e in self.examples],
            "tools_required": self.tools_required,
            "tags": self.tags,
            "source_dream_ids": self.source_dream_ids,
            "source_memory_ids": self.source_memory_ids,
            "source_task_type": self.source_task_type,
            "confidence": self.confidence,
            "usage_count": self.usage_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "version": self.version,
            "improvement_count": self.improvement_count,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "last_improved_at": self.last_improved_at,
            "reviewed_at": self.reviewed_at,
            "status": self.status,
            "managed_by": self.managed_by,
            "manual_override": self.manual_override,
            "feedback_history": self.feedback_history,
        }
