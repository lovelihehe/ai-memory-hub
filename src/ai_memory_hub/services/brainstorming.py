"""
Brainstorming 探索服务。

管理 Brainstorming 层（灵感层），整合 Dream 生成的内容：
- Dreams（梦境）：AI 会话的洞见集合
- Questions（问题）：待探索的问题（来自 Dream follow_ups）
- Connections（连接）：Dream ↔ Memory 关联关系图

参考 llm-knowledge-base 的 brainstorming 层设计，
但整合到现有的 AI Memory Hub 架构中。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.dream_models import Dream, DreamConnections
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.core.utils import ensure_parent, slugify
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.storage.dream_store import DreamStore


class BrainstormingService:
    """Brainstorming 服务。"""

    BRAINSTORMING_ROOT = "brainstorming"
    DREAMS_DIR = "dreams"
    QUESTIONS_DIR = "questions"
    CONNECTIONS_DIR = "connections"
    INDEX_FILE = "index.json"

    def __init__(self, config: MemoryConfig):
        self.config = config
        self.root = config.data_home_path / self.BRAINSTORMING_ROOT
        self.dreams_dir = self.root / self.DREAMS_DIR
        self.questions_dir = self.root / self.QUESTIONS_DIR
        self.connections_dir = self.root / self.CONNECTIONS_DIR
        self.index_path = self.root / self.INDEX_FILE
        self.logger = get_logger(config.data_home_path / "logs")
        self._store = MemoryStore(config)
        self._dream_store = DreamStore(config)

    def ensure_layout(self) -> None:
        """确保目录结构存在。"""
        for d in [self.dreams_dir, self.questions_dir, self.connections_dir]:
            d.mkdir(parents=True, exist_ok=True)
        self._dream_store.ensure_layout()

    def sync_dreams_to_brainstorming(self) -> dict[str, Any]:
        """将所有 Dreams 同步到 Brainstorming 目录。"""
        self.ensure_layout()
        dreams = self._dream_store.list_dreams(status="active")
        written = 0

        for dream in dreams:
            # 写入主文件
            dream_md_path = self.dreams_dir / f"{dream.id}.md"
            content = dream.to_markdown()
            if not dream_md_path.exists() or dream_md_path.read_text(encoding="utf-8") != content:
                ensure_parent(dream_md_path)
                dream_md_path.write_text(content, encoding="utf-8")
                written += 1

            # 写入推理过程（如果有）
            if dream.key_decisions or dream.sparks:
                reasoning_path = self.dreams_dir / f"{dream.id}-reasoning.md"
                reasoning_lines = [
                    f"# {dream.title} - 推理过程",
                    "",
                    f"**生成时间**: {dream.generated_at}",
                    "",
                ]
                if dream.key_decisions:
                    reasoning_lines.extend(["## 关键决策", ""])
                    for i, decision in enumerate(dream.key_decisions, 1):
                        reasoning_lines.append(f"{i}. {decision}")
                    reasoning_lines.append("")
                if dream.sparks:
                    reasoning_lines.extend(["## 知识碎片", ""])
                    for i, spark in enumerate(dream.sparks, 1):
                        reasoning_lines.append(f"### {i}. {spark.content}")
                        if spark.code_snippet:
                            lang = spark.language or ""
                            reasoning_lines.append(f"```{lang}")
                            reasoning_lines.append(spark.code_snippet)
                            reasoning_lines.append("```")
                        reasoning_lines.append("")
                ensure_parent(reasoning_path)
                reasoning_path.write_text("\n".join(reasoning_lines), encoding="utf-8")

            # 从 follow_ups 生成待探索问题
            for i, question in enumerate(dream.follow_ups):
                question_id = f"{dream.id}-q{i+1}"
                question_path = self.questions_dir / f"{question_id}.md"
                if not question_path.exists():
                    lines = [
                        f"# 待探索问题",
                        "",
                        f"**来源 Dream**: [{dream.title}](dreams/{dream.id}.md)",
                        f"**生成时间**: {dream.generated_at}",
                        f"**状态**: 待探索",
                        "",
                        f"## 问题",
                        "",
                        question,
                        "",
                        "## 探索记录",
                        "",
                        "_在此记录探索过程和结论..._",
                    ]
                    ensure_parent(question_path)
                    question_path.write_text("\n".join(lines), encoding="utf-8")

        self._update_brainstorming_index(dreams)

        return {
            "dreams_synced": len(dreams),
            "files_written": written,
        }

    def _update_brainstorming_index(self, dreams: list[Dream]) -> None:
        """更新 Brainstorming 索引。"""
        index: dict[str, Any] = {
            "generated_at": self._dream_store.new_dream_id() if False else "",
            "total_dreams": len(dreams),
            "dreams": [],
        }

        from ai_memory_hub.core.dream_models import utc_now
        index["generated_at"] = utc_now()

        for dream in sorted(dreams, key=lambda d: d.generated_at, reverse=True):
            index["dreams"].append({
                "id": dream.id,
                "title": dream.title,
                "category": dream.category,
                "generated_at": dream.generated_at,
                "summary": dream.summary,
                "insights_count": len(dream.insights),
                "sparks_count": len(dream.sparks),
                "follow_ups_count": len(dream.follow_ups),
                "related_memory_ids": dream.related_memory_ids,
            })

        self.index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def build_dream_connections(self) -> dict[str, Any]:
        """构建 Dream ↔ Memory 关联关系图。

        遍历所有 Dream 和 Memory，
        通过向量相似度或关键词匹配找到关联。
        """
        self.ensure_layout()
        connections: list[DreamConnections] = []

        memories = []
        for path in self._store.iter_memory_files():
            try:
                from ai_memory_hub.core.models import MemoryRecord
                payload = json.loads(path.read_text(encoding="utf-8"))
                memories.append(MemoryRecord.from_dict(payload))
            except Exception:
                continue

        if not memories:
            return {"connections_created": 0, "status": "no_memories"}

        dreams = self._dream_store.list_dreams(status="active")
        if not dreams:
            return {"connections_created": 0, "status": "no_dreams"}

        for dream in dreams:
            for memory in memories:
                score = self._calculate_connection_score(dream, memory)
                if score >= 0.3:
                    conn_type = "relates_to"
                    if score >= 0.6:
                        conn_type = "extends"
                    connections.append(DreamConnections(
                        dream_id=dream.id,
                        memory_id=memory.id,
                        connection_type=conn_type,
                        reason=f"相似度得分: {score:.2f}",
                    ))

        self._dream_store.write_connections(connections)
        self._write_connections_index(connections)

        return {
            "connections_created": len(connections),
            "dreams_processed": len(dreams),
            "memories_processed": len(memories),
            "status": "completed",
        }

    def _calculate_connection_score(self, dream: Dream, memory) -> float:
        """计算 Dream 与 Memory 之间的关联得分。"""
        score = 0.0

        dream_text = (
            dream.title + " " +
            " ".join(dream.insights) + " " +
            " ".join(d. content for d in dream.sparks)
        ).lower()
        memory_text = (
            memory.title + " " +
            memory.summary + " " +
            memory.details + " " +
            " ".join(memory.tags)
        ).lower()

        from difflib import SequenceMatcher
        sim = SequenceMatcher(None, dream_text, memory_text).ratio()
        if sim >= 0.3:
            score = sim

        memory_words = set(memory.title.lower().split())
        dream_words = set(dream.title.lower().split())
        overlap = memory_words & dream_words
        if len(overlap) >= 2:
            score = max(score, 0.4)

        if dream.related_project and memory.project_key:
            if dream.related_project == memory.project_key:
                score = max(score, 0.5)

        return score

    def _write_connections_index(self, connections: list[DreamConnections]) -> None:
        """写入连接索引。"""
        index_path = self.connections_dir / "index.json"
        by_dream: dict[str, list[dict[str, Any]]] = {}
        for conn in connections:
            by_dream.setdefault(conn.dream_id, []).append(conn.to_dict())
        index_path.write_text(
            json.dumps(by_dream, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_brainstorming_summary(self) -> dict[str, Any]:
        """获取 Brainstorming 层概览。"""
        dreams = self._dream_store.list_dreams(status="active")
        questions_count = len(list(self.questions_dir.glob("*.md"))) if self.questions_dir.exists() else 0

        category_counts: dict[str, int] = {}
        for dream in dreams:
            category_counts[dream.category] = category_counts.get(dream.category, 0) + 1

        connections_total = 0
        if self.connections_dir.exists():
            for path in self.connections_dir.glob("*.json"):
                if path.name != "index.json":
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(data, list):
                            connections_total += len(data)
                    except Exception:
                        pass

        return {
            "total_dreams": len(dreams),
            "category_counts": category_counts,
            "questions_pending": questions_count,
            "total_connections": connections_total,
            "recent_dreams": [
                {
                    "id": d.id,
                    "title": d.title,
                    "category": d.category,
                    "generated_at": d.generated_at,
                    "insights_count": len(d.insights),
                }
                for d in sorted(dreams, key=lambda x: x.generated_at, reverse=True)[:5]
            ],
        }

    def build_brainstorming_index(self) -> str:
        """构建 Brainstorming 索引页。"""
        self.ensure_layout()
        dreams = self._dream_store.list_dreams(status="active")
        summary = self.get_brainstorming_summary()

        lines = [
            "# Brainstorming 索引",
            "",
            f"**生成时间**: {summary.get('generated_at', '')}",
            "",
            "## 概览",
            f"- 活跃梦境数: {summary['total_dreams']}",
            f"- 待探索问题: {summary['questions_pending']}",
            f"- 总连接数: {summary['total_connections']}",
            "",
            "## 按类别统计",
        ]
        for cat, count in sorted(summary.get("category_counts", {}).items()):
            lines.append(f"- {cat}: {count}")
        lines.append("")
        lines.append("## 最近梦境")
        for dream in summary.get("recent_dreams", []):
            lines.append(f"- **{dream['title']}** ({dream['category']}, {dream['generated_at']})")
            lines.append(f"  - 洞见数: {dream['insights_count']}")
        lines.append("")
        lines.append("## 探索问题")

        if self.questions_dir.exists():
            for qpath in sorted(self.questions_dir.glob("*.md")):
                lines.append(f"- [[{qpath.stem}]]")
        else:
            lines.append("- 暂无待探索问题")

        content = "\n".join(lines)
        index_path = self.root / "index.md"
        index_path.write_text(content, encoding="utf-8")
        return str(index_path)


def run_brainstorming_sync(config: MemoryConfig | None = None) -> dict[str, Any]:
    """同步 Brainstorming 层。"""
    if config is None:
        from ai_memory_hub.core.config import load_config
        config = load_config()

    service = BrainstormingService(config)
    sync_result = service.sync_dreams_to_brainstorming()
    connections_result = service.build_dream_connections()
    index_path = service.build_brainstorming_index()

    return {
        **sync_result,
        **connections_result,
        "index_path": index_path,
    }
