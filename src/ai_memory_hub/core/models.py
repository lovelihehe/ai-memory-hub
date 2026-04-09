from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Self


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Evidence:
    source_tool: str
    source_path: str
    session_id: str | None
    timestamp: str | None
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> Self:
        if not isinstance(payload, dict):
            raise TypeError(f"Evidence.from_dict expects a dict, got {type(payload).__name__}")
        required_fields = ["source_tool", "source_path", "excerpt"]
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise KeyError(f"Evidence.from_dict missing required fields: {missing}")
        return Evidence(
            source_tool=payload["source_tool"],
            source_path=payload["source_path"],
            session_id=payload.get("session_id"),
            timestamp=payload.get("timestamp"),
            excerpt=payload["excerpt"],
        )


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
        if not isinstance(payload, dict):
            raise TypeError(f"MemoryRecord.from_dict expects a dict, got {type(payload).__name__}")
        # 必填字段验证
        required_fields = ["id", "title", "memory_type", "scope", "tool", "summary", "details", "confidence", "stability", "sensitivity", "created_at", "status"]
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise KeyError(f"MemoryRecord.from_dict missing required fields: {missing}")
        # 数值类型验证
        try:
            confidence = float(payload["confidence"])
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(f"confidence must be between 0.0 and 1.0, got {confidence}")
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid confidence value: {payload.get('confidence')}") from e
        try:
            stability = float(payload["stability"])
            if not 0.0 <= stability <= 1.0:
                raise ValueError(f"stability must be between 0.0 and 1.0, got {stability}")
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid stability value: {payload.get('stability')}") from e
        # evidence 格式验证
        evidence_data = payload.get("evidence", [])
        if not isinstance(evidence_data, list):
            raise TypeError(f"evidence must be a list, got {type(evidence_data).__name__}")
        return MemoryRecord(
            id=payload["id"],
            title=payload["title"],
            memory_type=payload["memory_type"],
            scope=payload["scope"],
            tool=payload["tool"],
            project_key=payload.get("project_key"),
            summary=payload["summary"],
            details=payload["details"],
            evidence=[Evidence(**item) for item in evidence_data],
            confidence=confidence,
            stability=stability,
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> Self:
        if not isinstance(payload, dict):
            raise TypeError(f"RawEvent.from_dict expects a dict, got {type(payload).__name__}")
        required_fields = ["id", "source_tool", "source_path", "event_type", "raw_json"]
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise KeyError(f"RawEvent.from_dict missing required fields: {missing}")
        return RawEvent(
            id=payload["id"],
            source_tool=payload["source_tool"],
            source_path=payload["source_path"],
            session_id=payload.get("session_id"),
            event_type=payload["event_type"],
            timestamp=payload.get("timestamp"),
            role=payload.get("role"),
            cwd=payload.get("cwd"),
            project_key=payload.get("project_key"),
            text=payload.get("text"),
            command=payload.get("command"),
            raw_json=payload["raw_json"],
        )
