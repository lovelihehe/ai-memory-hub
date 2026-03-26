"""
ChromaDB 向量存储模块。

提供语义搜索能力，基于 SentenceTransformer embedding。
支持懒加载，初始化失败时优雅降级。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_memory_hub.core.models import MemoryRecord


class VectorStore:
    def __init__(self, persist_dir: Path):
        self.persist_dir = persist_dir
        self._client: Any | None = None
        self._collection: Any | None = None
        self._embedding_model: Any | None = None
        self._available = True
        self._availability_error: str | None = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def availability_error(self) -> str | None:
        return self._availability_error

    def _disable(self, exc: Exception | str) -> bool:
        self._available = False
        self._availability_error = str(exc)
        self._client = None
        self._collection = None
        self._embedding_model = None
        return False

    def _ensure_ready(self) -> bool:
        if not self._available:
            return False
        if self._collection is not None and self._embedding_model is not None:
            return True
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer

            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.persist_dir))
            self._collection = self._client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )
            self._embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
            return True
        except Exception as exc:
            return self._disable(exc)

    def embed_text(self, text: str) -> list[float]:
        if not self._ensure_ready():
            raise RuntimeError(self._availability_error or "Vector store is unavailable.")
        return self._embedding_model.encode(text).tolist()

    def add_memory(self, memory: MemoryRecord) -> None:
        if not self._ensure_ready():
            return
        try:
            embed_text = f"{memory.title} {memory.summary} {memory.details}"
            embedding = self.embed_text(embed_text)
            self._collection.upsert(
                ids=[memory.id],
                embeddings=[embedding],
                metadatas=[{
                    "title": memory.title,
                    "memory_type": memory.memory_type,
                    "scope": memory.scope,
                    "tool": memory.tool,
                    "project_key": memory.project_key,
                    "summary": memory.summary,
                    "confidence": memory.confidence,
                    "stability": memory.stability,
                    "status": memory.status,
                    "tags": memory.tags,
                }],
            )
        except Exception as exc:
            self._disable(exc)

    def search_similar(self, query: str, limit: int = 10, filters: dict | None = None) -> list[dict]:
        if not query or not self._ensure_ready():
            return []
        try:
            query_embedding = self.embed_text(query)
            if filters:
                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=limit,
                    where=filters,
                )
            else:
                results = self._collection.query(
                    query_embeddings=[query_embedding],
                    n_results=limit,
                )
        except Exception as exc:
            self._disable(exc)
            return []

        formatted_results = []
        for memory_id, metadata, distance in zip(
            results["ids"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            formatted_results.append(
                {
                    "id": memory_id,
                    "title": metadata["title"],
                    "memory_type": metadata["memory_type"],
                    "scope": metadata["scope"],
                    "tool": metadata["tool"],
                    "project_key": metadata["project_key"],
                    "summary": metadata["summary"],
                    "confidence": metadata["confidence"],
                    "stability": metadata["stability"],
                    "status": metadata["status"],
                    "tags": metadata["tags"],
                    "similarity": 1 - distance,
                }
            )
        return formatted_results

    def update_memory(self, memory: MemoryRecord) -> None:
        self.add_memory(memory)

    def delete_memory(self, memory_id: str) -> None:
        if not self._ensure_ready():
            return
        try:
            self._collection.delete(ids=[memory_id])
        except Exception as exc:
            self._disable(exc)

    def clear(self) -> None:
        if not self._ensure_ready():
            return
        try:
            self._client.delete_collection("memories")
            self._collection = self._client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            self._disable(exc)
