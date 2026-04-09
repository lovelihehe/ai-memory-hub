"""
Dream（梦境）数据模型。

Dream 是 AI 会话的摘要形式，将每个会话视为一个「梦境」，
在会话结束时自动生成：
- 核心洞见（Insights）：从会话中提取的关键结论和发现
- 知识碎片（Sparks）：可复用的技术片段、代码模式、架构决策
- 待探索问题（Follow-ups）：需要进一步探索的问题
- 关联记忆（Related Memories）：与已有记忆的连接
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# 从 core.models 统一导入 utc_now，避免重复定义
from ai_memory_hub.core.models import utc_now


@dataclass(slots=True)
class Spark:
    """知识碎片：可复用的技术片段、代码模式或架构决策。"""
    content: str                              # 碎片内容
    code_snippet: str | None = None            # 代码片段（可选）
    language: str | None = None               # 代码语言
    source_excerpt: str | None = None        # 来源摘录
    tags: list[str] = field(default_factory=list)  # 标签

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "Spark":
        return Spark(
            content=payload["content"],
            code_snippet=payload.get("code_snippet"),
            language=payload.get("language"),
            source_excerpt=payload.get("source_excerpt"),
            tags=list(payload.get("tags", [])),
        )


@dataclass(slots=True)
class Dream:
    """
    Dream（梦境）：AI 会话的摘要与洞见集合。

    核心设计理念：
    - 每个 Dream 代表一个完整会话的「梦境记忆」
    - 粒度：完整会话洞见集合（vs MemoryRecord 单条独立记忆）
    - 生命周期：随会话结束自动生成（vs MemoryRecord 稳定性阈值激活）
    - 内容：探索/思考/发现（vs MemoryRecord 偏好/规则/模式）
    """
    id: str                                  # UUID，唯一标识
    source_session: str                      # 来源会话 ID
    source_tool: str                         # 来源工具 (codex/claude/cursor/gemini)
    source_path: str                         # 来源文件路径
    generated_at: str                        # 生成时间
    title: str                               # 梦境标题（如：「关于 RAG 架构的讨论」）
    category: str                            # 类别: exploration/creation/debug/learning/decision

    # 核心内容
    insights: list[str] = field(default_factory=list)        # 核心洞见（2-5条）
    sparks: list[Spark] = field(default_factory=list)       # 知识碎片
    follow_ups: list[str] = field(default_factory=list)     # 待探索问题
    key_decisions: list[str] = field(default_factory=list) # 关键决策

    # 关联
    related_memory_ids: list[str] = field(default_factory=list)  # 关联记忆 ID
    related_project: str | None = None                    # 关联项目

    # 元数据
    summary: str = ""                                      # 简短摘要
    message_count: int = 0                                # 会话消息数
    participant_tools: list[str] = field(default_factory=list)  # 参与的 AI 工具
    status: str = "active"                                 # 状态: active/archived/pinned

    # 追踪
    created_at: str = field(default_factory=lambda: utc_now())
    last_accessed_at: str | None = None
    tags: list[str] = field(default_factory=list)
    managed_by: str = "system"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sparks"] = [item.to_dict() for item in self.sparks]
        return payload

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "Dream":
        if not isinstance(payload, dict):
            raise TypeError(f"Dream.from_dict expects a dict, got {type(payload).__name__}")
        # 必填字段验证
        required_fields = ["id", "source_session", "source_tool", "source_path", "generated_at", "title", "category"]
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise KeyError(f"Dream.from_dict missing required fields: {missing}")
        # message_count 必须是整数
        try:
            message_count = int(payload.get("message_count", 0))
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid message_count value: {payload.get('message_count')}") from e
        # sparks 格式验证
        sparks_data = payload.get("sparks", [])
        if not isinstance(sparks_data, list):
            raise TypeError(f"sparks must be a list, got {type(sparks_data).__name__}")
        return Dream(
            id=payload["id"],
            source_session=payload["source_session"],
            source_tool=payload["source_tool"],
            source_path=payload["source_path"],
            generated_at=payload["generated_at"],
            title=payload["title"],
            category=payload["category"],
            insights=list(payload.get("insights", [])),
            sparks=[Spark.from_dict(item) for item in sparks_data],
            follow_ups=list(payload.get("follow_ups", [])),
            key_decisions=list(payload.get("key_decisions", [])),
            related_memory_ids=list(payload.get("related_memory_ids", [])),
            related_project=payload.get("related_project"),
            summary=payload.get("summary", ""),
            message_count=message_count,
            participant_tools=list(payload.get("participant_tools", [])),
            status=payload.get("status", "active"),
            created_at=payload.get("created_at", utc_now()),
            last_accessed_at=payload.get("last_accessed_at"),
            tags=list(payload.get("tags", [])),
            managed_by=payload.get("managed_by", "system"),
        )

    def to_markdown(self) -> str:
        """将 Dream 渲染为 Markdown 格式。"""
        lines = [
            f"# {self.title}",
            "",
            f"**类别**: {self.category}",
            f"**工具**: {', '.join(self.participant_tools) if self.participant_tools else self.source_tool}",
            f"**生成时间**: {self.generated_at}",
            f"**消息数**: {self.message_count}",
        ]
        if self.related_project:
            lines.append(f"**项目**: {self.related_project}")
        if self.summary:
            lines.extend(["", f"**摘要**: {self.summary}"])

        if self.insights:
            lines.extend(["", "## 核心洞见"])
            for i, insight in enumerate(self.insights, 1):
                lines.append(f"{i}. {insight}")

        if self.key_decisions:
            lines.extend(["", "## 关键决策"])
            for decision in self.key_decisions:
                lines.append(f"- {decision}")

        if self.sparks:
            lines.extend(["", "## 知识碎片"])
            for i, spark in enumerate(self.sparks, 1):
                lines.append(f"### {i}. {spark.content}")
                if spark.code_snippet:
                    lang = spark.language or ""
                    lines.append(f"```{lang}")
                    lines.append(spark.code_snippet)
                    lines.append("```")
                if spark.tags:
                    lines.append(f"标签: {', '.join(spark.tags)}")
                lines.append("")

        if self.follow_ups:
            lines.extend(["", "## 待探索问题"])
            for question in self.follow_ups:
                lines.append(f"- {question}")

        if self.related_memory_ids:
            lines.extend(["", "## 关联记忆"])
            for mem_id in self.related_memory_ids:
                lines.append(f"- [[{mem_id}]]")

        if self.tags:
            lines.extend(["", f"**标签**: {', '.join(self.tags)}"])

        return "\n".join(lines).rstrip() + "\n"


@dataclass(slots=True)
class DreamConnections:
    """Dream 与 Memory 之间的关联关系图。"""
    dream_id: str
    memory_id: str
    connection_type: str  # "relates_to" / "extends" / "contradicts" / "references"
    reason: str           # 关联原因
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "DreamConnections":
        return DreamConnections(
            dream_id=payload["dream_id"],
            memory_id=payload["memory_id"],
            connection_type=payload["connection_type"],
            reason=payload["reason"],
            created_at=payload.get("created_at", utc_now()),
        )
