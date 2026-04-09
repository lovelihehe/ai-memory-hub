"""
Dream 存储层。

负责 Dream（梦境）和 DreamConnections 的持久化存储，
提供：
- Dream CRUD 操作
- Dreams 索引管理
- 向量存储集成
- 与 MemoryRecord 的关联管理
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.dream_models import Dream, DreamConnections, Spark
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.core.utils import ensure_parent


class DreamStore:
    """Dream 存储引擎。"""

    DREAM_DIR = "dreams"
    CONNECTIONS_DIR = "connections"
    INDEX_FILE = "index.json"
    # 批量写入阈值：累积这么多更新后再一次性刷新索引
    _INDEX_FLUSH_THRESHOLD = 50

    def __init__(self, config: MemoryConfig):
        self.config = config
        self.root = config.data_home_path
        self.dreams_root = self.root / self.DREAM_DIR
        self.connections_root = self.root / self.CONNECTIONS_DIR
        self.index_path = self.dreams_root / self.INDEX_FILE
        self.logger = get_logger(self.root / "logs")
        # 内存中的索引缓存，避免每次写入都读文件
        self._index_cache: dict[str, dict[str, Any]] | None = None
        self._index_dirty = False
        self._pending_updates = 0

    def ensure_layout(self) -> None:
        """确保目录结构存在。"""
        for relative in [self.DREAM_DIR, self.CONNECTIONS_DIR]:
            (self.root / relative).mkdir(parents=True, exist_ok=True)

    def _dream_path(self, dream_id: str) -> Path:
        return self.dreams_root / f"{dream_id}.json"

    def _connections_path(self, dream_id: str) -> Path:
        return self.connections_root / f"{dream_id}.json"

    def write_dream(self, dream: Dream) -> Path:
        """写入 Dream 到文件系统。"""
        self.ensure_layout()
        path = self._dream_path(dream.id)
        ensure_parent(path)
        path.write_text(
            json.dumps(dream.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.info(f"Written dream: {dream.id} ({dream.category}) - {dream.title}")
        self._update_index(dream)
        return path

    def load_dream(self, dream_id: str) -> Dream | None:
        """按 ID 加载 Dream。"""
        path = self._dream_path(dream_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return Dream.from_dict(payload)
        except Exception:
            return None

    def delete_dream(self, dream_id: str) -> bool:
        """删除 Dream。"""
        path = self._dream_path(dream_id)
        if path.exists():
            path.unlink()
        connections_path = self._connections_path(dream_id)
        if connections_path.exists():
            connections_path.unlink()
        self._remove_from_index(dream_id)
        return True

    def list_dreams(self, status: str | None = None, category: str | None = None) -> list[Dream]:
        """列出 Dreams，支持按状态和类别过滤。"""
        dreams: list[Dream] = []
        if not self.dreams_root.exists():
            return dreams
        for path in sorted(self.dreams_root.glob("*.json")):
            if path.name == self.INDEX_FILE:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                dream = Dream.from_dict(payload)
                if status and dream.status != status:
                    continue
                if category and dream.category != category:
                    continue
                dreams.append(dream)
            except Exception:
                continue
        return sorted(dreams, key=lambda d: d.generated_at, reverse=True)

    def search_dreams(self, query: str, limit: int = 10) -> list[Dream]:
        """在 Dreams 中进行全文搜索。"""
        dreams = self.list_dreams(status="active")
        if not query.strip():
            return dreams[:limit]
        query_lower = query.lower()
        scored: list[tuple[float, Dream]] = []
        for dream in dreams:
            score = 0.0
            if query_lower in dream.title.lower():
                score += 3.0
            if query_lower in dream.summary.lower():
                score += 2.0
            for insight in dream.insights:
                if query_lower in insight.lower():
                    score += 1.5
            for spark in dream.sparks:
                if query_lower in spark.content.lower():
                    score += 1.0
            if query_lower in dream.category.lower():
                score += 0.5
            for tag in dream.tags:
                if query_lower in tag.lower():
                    score += 0.5
            if score > 0:
                scored.append((score, dream))
        scored.sort(key=lambda item: -item[0])
        return [dream for _, dream in scored[:limit]]

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
            self.logger.warning(f"Failed to persist dream index: {e}")

    def _update_index(self, dream: Dream, immediate: bool = False) -> None:
        """更新 Dreams 索引文件（使用内存缓存避免 O(n²)）。"""
        self._ensure_index_cache()
        if self._index_cache is None:
            return
        self._index_cache[dream.id] = {
            "title": dream.title,
            "category": dream.category,
            "status": dream.status,
            "generated_at": dream.generated_at,
            "summary": dream.summary,
            "tags": dream.tags,
            "related_memory_ids": dream.related_memory_ids,
            "related_project": dream.related_project,
            "message_count": dream.message_count,
        }
        self._index_dirty = True
        self._pending_updates += 1
        # 超过阈值或强制立即写入时刷新
        if immediate or self._pending_updates >= self._INDEX_FLUSH_THRESHOLD:
            self._persist_index()
            self._pending_updates = 0

    def _remove_from_index(self, dream_id: str) -> None:
        """从索引中移除 Dream。"""
        self._ensure_index_cache()
        if self._index_cache is None or not self.index_path.exists():
            return
        try:
            self._index_cache.pop(dream_id, None)
            self._index_dirty = True
            self._persist_index()
        except Exception as e:
            self.logger.warning(f"Failed to remove dream from index: {e}")

    def flush_index(self) -> None:
        """强制将内存中的索引写入磁盘。"""
        self._persist_index()
        self._pending_updates = 0

    def get_index(self) -> dict[str, dict[str, Any]]:
        """获取 Dreams 索引（从缓存读取）。"""
        self._ensure_index_cache()
        return self._index_cache if self._index_cache is not None else {}

    # ── Connections ────────────────────────────────────────────

    def write_connections(self, connections: list[DreamConnections]) -> None:
        """写入 Dream 与 Memory 之间的关联。"""
        self.ensure_layout()
        by_dream: dict[str, list[dict[str, Any]]] = {}
        for conn in connections:
            by_dream.setdefault(conn.dream_id, []).append(conn.to_dict())
        for dream_id, conn_list in by_dream.items():
            path = self._connections_path(dream_id)
            path.write_text(
                json.dumps(conn_list, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def get_connections_for_dream(self, dream_id: str) -> list[DreamConnections]:
        """获取某个 Dream 的所有关联。"""
        path = self._connections_path(dream_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [DreamConnections.from_dict(item) for item in data]
        except Exception:
            return []

    def get_connections_for_memory(self, memory_id: str) -> list[DreamConnections]:
        """获取关联到某个 Memory 的所有 Dream 连接。"""
        connections: list[DreamConnections] = []
        if not self.connections_root.exists():
            return connections
        for path in self.connections_root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for item in data:
                    if item.get("memory_id") == memory_id:
                        connections.append(DreamConnections.from_dict(item))
            except Exception:
                continue
        return connections

    # ── 统计 ──────────────────────────────────────────────────

    def count_dreams(self, status: str | None = None) -> int:
        """统计 Dreams 数量。"""
        return len(self.list_dreams(status=status))

    def get_dreams_by_project(self, project_key: str) -> list[Dream]:
        """按项目获取 Dreams。"""
        return [d for d in self.list_dreams(status="active") if d.related_project == project_key]

    def get_recent_dreams(self, limit: int = 10) -> list[Dream]:
        """获取最近的 Dreams。"""
        return self.list_dreams(status="active")[:limit]

    # ── 生成新 Dream ──────────────────────────────────────────

    @staticmethod
    def new_dream_id() -> str:
        """生成新的 Dream ID。"""
        return f"dream-{uuid.uuid4().hex[:12]}"

    def create_dream(
        self,
        source_session: str,
        source_tool: str,
        source_path: str,
        title: str,
        category: str,
        insights: list[str],
        sparks: list[Spark] | None = None,
        follow_ups: list[str] | None = None,
        key_decisions: list[str] | None = None,
        summary: str = "",
        message_count: int = 0,
        participant_tools: list[str] | None = None,
        related_project: str | None = None,
        tags: list[str] | None = None,
    ) -> Dream:
        """创建新的 Dream。"""
        from ai_memory_hub.core.dream_models import utc_now as dream_utc_now
        dream = Dream(
            id=self.new_dream_id(),
            source_session=source_session,
            source_tool=source_tool,
            source_path=source_path,
            generated_at=dream_utc_now(),
            title=title,
            category=category,
            insights=insights,
            sparks=sparks or [],
            follow_ups=follow_ups or [],
            key_decisions=key_decisions or [],
            summary=summary,
            message_count=message_count,
            participant_tools=participant_tools or [source_tool],
            related_project=related_project,
            tags=tags or [],
            status="active",
        )
        return dream
