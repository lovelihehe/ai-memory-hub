"""
SQLite 数据库存储模块。

负责记忆记录、原始事件的持久化，提供：
- SQLite 连接管理
- 记忆 CRUD 操作
- FTS5 全文索引
- 数据导入导出（ZIP 格式）
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from ai_memory_hub.core.config import MemoryConfig
from ai_memory_hub.core.logger import get_logger
from ai_memory_hub.core.models import MemoryRecord, RawEvent
from ai_memory_hub.core.utils import ensure_parent
from ai_memory_hub.storage.vector import VectorStore


TYPE_DIR_MAP = {
    "profile": "profile",
    "procedural": "procedures",
    "episodic": "episodes",
    "semantic": "patterns",
}


class MemoryStore:
    def __init__(self, config: MemoryConfig):
        self.config = config
        self.root = config.data_home_path
        self.index_root = self.root / "index"
        self.db_path = self.index_root / "memory.db"
        self.logger = get_logger(self.root / "logs")
        self._vector_store: VectorStore | None = None
        self._vector_store_disabled = False

    def ensure_layout(self) -> None:
        for relative in [
            "profile",
            "procedures",
            "episodes",
            "patterns",
            "projects",
            "inbox",
            "index",
            "rendered",
            "state",
            "notes",
            "rules",
        ]:
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self):
        self.ensure_layout()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        ensure_parent(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            create table if not exists source_cursors (
              source_path text primary key,
              file_size integer not null default 0,
              position integer not null default 0,
              modified_at text
            );

            create table if not exists raw_events (
              id text primary key,
              source_tool text not null,
              source_path text not null,
              session_id text,
              event_type text not null,
              timestamp text,
              role text,
              cwd text,
              project_key text,
              text text,
              command text,
              raw_json text not null
            );

            create table if not exists memories (
              id text primary key,
              title text not null,
              memory_type text not null,
              scope text not null,
              tool text not null,
              project_key text,
              summary text not null,
              details text not null,
              evidence_json text not null,
              confidence real not null,
              stability real not null,
              sensitivity text not null,
              tags_json text not null,
              created_at text not null,
              last_seen_at text,
              reviewed_at text,
              status text not null,
              supersedes text,
              file_path text not null,
              managed_by text not null default 'system',
              manual_override integer not null default 0
            );

            create virtual table if not exists memory_fts using fts5(
              id,
              title,
              summary,
              details,
              tags,
              tokenize = 'unicode61'
            );
            """
        )
        self._migrate_db(conn)
        conn.commit()
        conn.close()

    def _migrate_db(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "last_accessed_at" not in columns:
            conn.execute("alter table memories add column last_accessed_at text")
        if "expiration_days" not in columns:
            conn.execute("alter table memories add column expiration_days integer not null default 90")
        if "usage_count" not in columns:
            conn.execute("alter table memories add column usage_count integer not null default 0")

    def get_vector_store(self) -> "VectorStore | None":
        if os.environ.get("AI_MEMORY_ENABLE_VECTOR") != "1":
            return None
        if self._vector_store_disabled:
            return None
        if self._vector_store is None:
            store = VectorStore(self.index_root / "vector")
            if not store.available and store.availability_error:
                self.logger.warning(f"Vector store disabled: {store.availability_error}")
                self._vector_store_disabled = True
                return None
            self._vector_store = store
        return self._vector_store

    def get_cursor_state(self, source_path: str) -> tuple[int, int]:
        with self.connect() as conn:
            row = conn.execute(
                "select position, file_size from source_cursors where source_path = ?",
                (source_path,),
            ).fetchone()
            if not row:
                return 0, 0
            return int(row["position"]), int(row["file_size"])

    def set_cursor_state(self, source_path: str, position: int, file_size: int, modified_at: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into source_cursors(source_path, position, file_size, modified_at)
                values(?, ?, ?, ?)
                on conflict(source_path) do update set
                  position = excluded.position,
                  file_size = excluded.file_size,
                  modified_at = excluded.modified_at
                """,
                (source_path, position, file_size, str(modified_at) if modified_at is not None else None),
            )
            conn.commit()

    def insert_raw_events(self, events: list[RawEvent]) -> int:
        if not events:
            return 0
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                insert or ignore into raw_events(
                  id, source_tool, source_path, session_id, event_type, timestamp,
                  role, cwd, project_key, text, command, raw_json
                )
                values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.id,
                        item.source_tool,
                        item.source_path,
                        item.session_id,
                        item.event_type,
                        item.timestamp,
                        item.role,
                        item.cwd,
                        item.project_key,
                        item.text,
                        item.command,
                        item.raw_json,
                    )
                    for item in events
                ],
            )
            conn.commit()
            return conn.total_changes - before

    def list_raw_events(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                select * from raw_events
                order by coalesce(timestamp, ''), source_path, id
                """
            ).fetchall()

    def count_memories(self, *, status: str | None = None) -> int:
        with self.connect() as conn:
            if status:
                row = conn.execute("select count(*) as total from memories where status = ?", (status,)).fetchone()
            else:
                row = conn.execute("select count(*) as total from memories").fetchone()
        return int(row["total"]) if row else 0

    def memory_path_for(self, record: MemoryRecord) -> Path:
        if record.status == "candidate":
            return self.root / "inbox" / f"{record.id}.json"
        if record.scope == "project" and record.project_key:
            return self.root / "projects" / record.project_key / "memories" / f"{record.id}.json"
        target_dir = TYPE_DIR_MAP.get(record.memory_type, "patterns")
        return self.root / target_dir / f"{record.id}.json"

    def write_memory(self, record: MemoryRecord) -> Path:
        path = self.memory_path_for(record)
        for existing in self.root.rglob(f"{record.id}.json"):
            if existing != path and existing.exists():
                existing.unlink()
        ensure_parent(path)
        path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        vector_store = self.get_vector_store()
        if vector_store is not None:
            vector_store.add_memory(record)
        self.logger.info(f"Written memory: {record.id} ({record.memory_type}, {record.status})")
        return path

    def load_memory(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as conn:
            row = conn.execute("select file_path from memories where id = ?", (memory_id,)).fetchone()
        if not row:
            candidate_path = self.root / "inbox" / f"{memory_id}.json"
            if candidate_path.exists():
                return MemoryRecord.from_dict(json.loads(candidate_path.read_text(encoding="utf-8")))
            return None
        path = Path(row["file_path"])
        if not path.exists():
            return None
        return MemoryRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def iter_memory_files(self) -> list[Path]:
        paths: list[Path] = []
        for relative in ["profile", "procedures", "episodes", "patterns", "inbox", "projects"]:
            root = self.root / relative
            if root.exists():
                paths.extend(root.rglob("*.json"))
        return sorted(paths)

    def rebuild_memory_index(self, incremental: bool = False) -> int:
        files = self.iter_memory_files()
        self.logger.info(f"Rebuilding memory index: {len(files)} files, incremental={incremental}")
        vector_store = self.get_vector_store()
        with self.connect() as conn:
            if not incremental:
                conn.execute("delete from memories")
                conn.execute("delete from memory_fts")
                if vector_store is not None:
                    vector_store.clear()
                    self.logger.info("Cleared existing vector index for full rebuild")

            processed_files: set[str] = set()
            for path in files:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    record = MemoryRecord.from_dict(payload)
                    file_path_str = str(path)

                    if incremental:
                        existing = conn.execute("select file_path from memories where id = ?", (record.id,)).fetchone()
                        if existing:
                            conn.execute(
                                """
                                update memories set
                                  title = ?, memory_type = ?, scope = ?, tool = ?, project_key = ?,
                                  summary = ?, details = ?, evidence_json = ?,
                                  confidence = ?, stability = ?, sensitivity = ?, tags_json = ?,
                                  created_at = ?, last_seen_at = ?, reviewed_at = ?, status = ?, supersedes = ?,
                                  file_path = ?, managed_by = ?, manual_override = ?, last_accessed_at = ?, expiration_days = ?
                                where id = ?
                                """,
                                (
                                    record.title,
                                    record.memory_type,
                                    record.scope,
                                    record.tool,
                                    record.project_key,
                                    record.summary,
                                    record.details,
                                    json.dumps([asdict(item) for item in record.evidence], ensure_ascii=False),
                                    record.confidence,
                                    record.stability,
                                    record.sensitivity,
                                    json.dumps(record.tags, ensure_ascii=False),
                                    record.created_at,
                                    record.last_seen_at,
                                    record.reviewed_at,
                                    record.status,
                                    record.supersedes,
                                    file_path_str,
                                    record.managed_by,
                                    1 if record.manual_override else 0,
                                    record.last_accessed_at,
                                    record.expiration_days,
                                    record.id,
                                ),
                            )
                            conn.execute("delete from memory_fts where id = ?", (record.id,))
                        else:
                            conn.execute(
                                """
                                insert into memories(
                                  id, title, memory_type, scope, tool, project_key, summary, details,
                                  evidence_json, confidence, stability, sensitivity, tags_json,
                                  created_at, last_seen_at, reviewed_at, status, supersedes, file_path,
                                  managed_by, manual_override, last_accessed_at, expiration_days
                                )
                                values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    record.id,
                                    record.title,
                                    record.memory_type,
                                    record.scope,
                                    record.tool,
                                    record.project_key,
                                    record.summary,
                                    record.details,
                                    json.dumps([asdict(item) for item in record.evidence], ensure_ascii=False),
                                    record.confidence,
                                    record.stability,
                                    record.sensitivity,
                                    json.dumps(record.tags, ensure_ascii=False),
                                    record.created_at,
                                    record.last_seen_at,
                                    record.reviewed_at,
                                    record.status,
                                    record.supersedes,
                                    file_path_str,
                                    record.managed_by,
                                    1 if record.manual_override else 0,
                                    record.last_accessed_at,
                                    record.expiration_days,
                                ),
                            )
                    else:
                        conn.execute(
                            """
                            insert into memories(
                              id, title, memory_type, scope, tool, project_key, summary, details,
                              evidence_json, confidence, stability, sensitivity, tags_json,
                              created_at, last_seen_at, reviewed_at, status, supersedes, file_path,
                              managed_by, manual_override, last_accessed_at, expiration_days
                            )
                            values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                record.id,
                                record.title,
                                record.memory_type,
                                record.scope,
                                record.tool,
                                record.project_key,
                                record.summary,
                                record.details,
                                json.dumps([asdict(item) for item in record.evidence], ensure_ascii=False),
                                record.confidence,
                                record.stability,
                                record.sensitivity,
                                json.dumps(record.tags, ensure_ascii=False),
                                record.created_at,
                                record.last_seen_at,
                                record.reviewed_at,
                                record.status,
                                record.supersedes,
                                file_path_str,
                                record.managed_by,
                                1 if record.manual_override else 0,
                                record.last_accessed_at,
                                record.expiration_days,
                            ),
                        )

                    conn.execute(
                        "insert or replace into memory_fts(id, title, summary, details, tags) values(?, ?, ?, ?, ?)",
                        (
                            record.id,
                            record.title,
                            record.summary,
                            record.details,
                            " ".join(record.tags),
                        ),
                    )
                    if vector_store is not None:
                        vector_store.add_memory(record)
                    processed_files.add(file_path_str)
                except Exception:
                    continue

            if incremental:
                existing_files = {row[0] for row in conn.execute("select file_path from memories").fetchall()}
                for file_path in existing_files - processed_files:
                    id_row = conn.execute("select id from memories where file_path = ?", (file_path,)).fetchone()
                    if id_row:
                        memory_id = id_row[0]
                        if vector_store is not None:
                            vector_store.delete_memory(memory_id)
                        conn.execute("delete from memory_fts where id = ?", (memory_id,))
                    conn.execute("delete from memories where file_path = ?", (file_path,))
            conn.commit()
        self.logger.info(f"Index rebuild completed: {len(files)} files processed")
        return len(files)

    def update_memory_access(self, memory_id: str, increment: bool = True) -> None:
        from ai_memory_hub.core.models import utc_now

        with self.connect() as conn:
            if increment:
                conn.execute(
                    "update memories set last_accessed_at = ?, usage_count = usage_count + 1 where id = ?",
                    (utc_now(), memory_id),
                )
            else:
                conn.execute(
                    "update memories set last_accessed_at = ? where id = ?",
                    (utc_now(), memory_id),
                )
            conn.commit()

    def batch_update_access(self, memory_ids: list[str]) -> None:
        from ai_memory_hub.core.models import utc_now

        if not memory_ids:
            return
        with self.connect() as conn:
            now = utc_now()
            for mid in memory_ids:
                conn.execute(
                    "update memories set last_accessed_at = ?, usage_count = usage_count + 1 where id = ?",
                    (now, mid),
                )
            conn.commit()

    def cleanup_expired_memories(self) -> int:
        expired_count = 0
        self.logger.info("Starting cleanup of expired memories")
        vector_store = self.get_vector_store()
        with self.connect() as conn:
            cursor = conn.execute("PRAGMA table_info(memories)")
            columns = [row[1] for row in cursor.fetchall()]
            if "expiration_days" in columns:
                rows = conn.execute("select id, created_at, expiration_days, file_path from memories").fetchall()
            else:
                rows = conn.execute("select id, created_at, 90 as expiration_days, file_path from memories").fetchall()

            for row in rows:
                created_value = row["created_at"]
                if isinstance(created_value, str) and created_value.isdigit():
                    timestamp = int(created_value)
                    created_date = datetime.fromtimestamp(
                        timestamp / 1000 if len(created_value) >= 13 else timestamp,
                        tz=timezone.utc,
                    )
                else:
                    created_date = datetime.fromisoformat(str(created_value).replace("Z", "+00:00"))
                current_date = datetime.now(timezone.utc)
                age_days = (current_date - created_date).days
                if age_days <= row["expiration_days"]:
                    continue
                try:
                    path = Path(row["file_path"])
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
                if vector_store is not None:
                    vector_store.delete_memory(row["id"])
                conn.execute("delete from memories where id = ?", (row["id"],))
                conn.execute("delete from memory_fts where id = ?", (row["id"],))
                expired_count += 1
            conn.commit()

        self.logger.info(f"Cleanup completed: {expired_count} expired memories removed")
        return expired_count

    def export_data(self, export_path: Path) -> dict[str, int]:
        export_count = 0
        with ZipFile(export_path, "w", ZIP_DEFLATED) as zipf:
            for memory_file in self.iter_memory_files():
                try:
                    rel_path = memory_file.relative_to(self.root)
                    zipf.write(memory_file, str(rel_path))
                    export_count += 1
                except Exception:
                    continue
            config_path = self.root.parent / "config.json"
            if config_path.exists():
                zipf.write(config_path, "config.json")
        return {"exported_memories": export_count}

    def import_data(self, import_path: Path) -> dict[str, int]:
        import_count = 0
        skipped_files = 0
        with ZipFile(import_path, "r") as zipf:
            for file_info in zipf.infolist():
                if file_info.is_dir():
                    continue
                target_path = self._safe_import_target(file_info.filename)
                if target_path is None:
                    skipped_files += 1
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zipf.open(file_info) as src, open(target_path, "wb") as dst:
                    dst.write(src.read())
                if file_info.filename.endswith(".json") and file_info.filename != "config.json":
                    import_count += 1
        self.rebuild_memory_index()
        return {"imported_memories": import_count, "skipped_files": skipped_files}

    def _safe_import_target(self, archived_name: str) -> Path | None:
        normalized = archived_name.replace("\\", "/").strip("/")
        if normalized == "config.json":
            return (self.root.parent / "config.json").resolve()
        if not normalized:
            return None
        candidate = (self.root / normalized).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:
            return None
        return candidate
