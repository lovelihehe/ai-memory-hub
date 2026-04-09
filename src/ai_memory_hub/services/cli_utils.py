"""
CLI 工具函数模块。

提供 CLI 命令中常用的公共初始化和工具函数。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_memory_hub.core.config import MemoryConfig
    from ai_memory_hub.storage.db import MemoryStore


def load_config_and_store() -> tuple[MemoryConfig, MemoryStore]:
    """
    加载配置并创建 MemoryStore 实例。

    这是 CLI 命令中最常用的初始化模式，返回 (config, store) 元组。
    """
    from ai_memory_hub.core.config import load_config
    from ai_memory_hub.storage.db import MemoryStore

    config = load_config()
    store = MemoryStore(config)
    return config, store


def ensure_path_exists(path: Path, is_file: bool = False) -> Path:
    """
    确保路径存在。

    如果 is_file 为 True，确保父目录存在。
    如果 is_file 为 False，确保目录本身存在。
    """
    if is_file:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)
    return path


def validate_memory_id(memory_id: str | None) -> str | None:
    """
    验证记忆 ID 格式。

    返回验证通过的 ID，如果无效则返回 None。
    """
    if not memory_id:
        return None
    if len(memory_id) < 8 or len(memory_id) > 64:
        return None
    return memory_id


def validate_action(action: str) -> str | None:
    """
    验证 feedback action 是否合法。

    合法的 action 值：
    - promote, confirm: 将记忆提升为 active
    - demote: 将记忆降级为 candidate
    - archive: 归档记忆
    - contradict: 标记为矛盾
    - merge: 合并记忆
    """
    valid_actions = {"promote", "confirm", "demote", "archive", "contradict", "merge"}
    if action in valid_actions:
        return action
    return None


def format_memory_summary(record: dict) -> str:
    """
    格式化记忆记录为可读摘要。

    用于 CLI 输出和日志记录。
    """
    status_emoji = {
        "active": "[+]",
        "candidate": "[?]",
        "archived": "[-]",
        "contradicted": "[!]",
    }
    emoji = status_emoji.get(record.get("status", ""), "[?]")
    title = record.get("title", "Untitled")[:50]
    confidence = record.get("confidence", 0.0)
    return f"{emoji} {title} (conf={confidence:.2f})"
