from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Evidence:
    source_tool: str
    source_path: str
    session_id: str | None
    timestamp: str | None
    excerpt: str


@dataclass(slots=True)
class MemoryRecord:
    id: str
    title: str
    memory_type: str
    scope: str
    tool: str
    project_key: str | None
    summary: str
    details: str
    evidence: list[Evidence]
    confidence: float
    stability: float
    sensitivity: str
    tags: list[str]
    created_at: str
    last_seen_at: str | None
    reviewed_at: str | None
    status: str
    supersedes: str | None
    managed_by: str = "system"
    manual_override: bool = False
    last_accessed_at: str | None = None
    expiration_days: int = 90

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [asdict(item) for item in self.evidence]
        return payload

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "MemoryRecord":
        return MemoryRecord(
            id=payload["id"],
            title=payload["title"],
            memory_type=payload["memory_type"],
            scope=payload["scope"],
            tool=payload["tool"],
            project_key=payload.get("project_key"),
            summary=payload["summary"],
            details=payload["details"],
            evidence=[Evidence(**item) for item in payload.get("evidence", [])],
            confidence=float(payload["confidence"]),
            stability=float(payload["stability"]),
            sensitivity=payload["sensitivity"],
            tags=list(payload.get("tags", [])),
            created_at=payload["created_at"],
            last_seen_at=payload.get("last_seen_at"),
            reviewed_at=payload.get("reviewed_at"),
            status=payload["status"],
            supersedes=payload.get("supersedes"),
            managed_by=payload.get("managed_by", "system"),
            manual_override=bool(payload.get("manual_override", False)),
            last_accessed_at=payload.get("last_accessed_at"),
            expiration_days=int(payload.get("expiration_days", 90)),
        )


@dataclass(slots=True)
class RawEvent:
    id: str
    source_tool: str
    source_path: str
    session_id: str | None
    event_type: str
    timestamp: str | None
    role: str | None
    cwd: str | None
    project_key: str | None
    text: str | None
    command: str | None
    raw_json: str
