"""
通用工具函数：ID 生成、文本规范、乱码检测、文件路径处理。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_memory_hub.core.models import MemoryRecord
    from ai_memory_hub.storage.db import MemoryStore

# GBK→UTF-8 双重编码乱码特征字符（用于 contains_mojibake 检测）
# 常见中文关键词被错误编码后出现的高频乱码片段
MOJIBAKE_MARKERS = (
    # 经典乱码
    "锟斤拷",  # NULL + GBK→UTF-8 经典乱码
    # 单字符残留
    "屯屯",  # 不
    "榛桦",  # 单字符残留
    "甯稿",  # 常常
    # 常见中文词双重编码
    "浠ュ悗",  # 以后
    "闇瑕",  # 隐私
    "绉侀挜",  # 密钥
    "鐠囪蒋",  # 密码
    "涓嶈",  # 不要
    "涓嶅",  # 不能
    "鍙绯绘",  # 系统
    "鐧诲綍",  # 登录
    "涓",  # 单字残留
    "鎴戝簲璇ユ妸",  # 我应该把
    "浣犵殑",  # 你的
    "璇风户鎴",  # 请继续
    "蹇呴'瑕",  # 必须先
    "鍏嶅",  # 免费
    "鎮ㄥ簲璇ユ湁",  # 您应该有
    "浠栧簲璇ユ湁",  # 他应该有
    "鎴栧簲璇ユ湁",  # 或应该有
)


def stable_id(*parts: str) -> str:
    """
    基于输入片段生成稳定（幂等）的短 ID（16 位十六进制）。
    用于 project_key、memory_id 等，保证同一内容每次生成的 ID 相同。
    """
    joined = "||".join(part.strip() for part in parts if part is not None)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def slugify(value: str) -> str:
    """
    将任意字符串规范化为 URL-safe slug（小写字母 + 数字，中划线连接）。
    例如："My Project 01" -> "my-project-01"
    """
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


def project_key_from_path(path: str | None) -> str | None:
    """
    从目录路径生成唯一的 project_key，格式为 `{slug}-{16位hash}`。

    示例：
      "/Users/me/my-project"  -> "my-project-a1b2c3d4e5f6g7h8"
      "C:\\Users\\me\\my-project" -> "my-project-a1b2c3d4e5f6g7h8"
      "~/my-project"           -> "my-project-a1b2c3d4e5f6g7h8"
    路径规范化：~ 展开、反斜杠转正斜杠、去除末尾斜杠，取最后一层目录名。
    """
    if not path:
        return None
    # 展开 ~ 为用户主目录
    cleaned = str(Path(path).expanduser())
    # 统一正斜杠
    cleaned = cleaned.replace("\\", "/").rstrip("/")
    base = cleaned.split("/")[-1] or "project"
    return f"{slugify(base)}-{stable_id(cleaned)}"


def normalize_project_reference(value: str | None) -> str | None:
    """
    统一项目引用格式：
      - 已是标准格式（slug-hash16）的直接返回
      - 是文件路径的转换为 project_key
      - 纯文本标签直接返回
    """
    if not value:
        return None
    if re.fullmatch(r"[a-z0-9-]+-[a-f0-9]{16}", value):
        return value
    # 路径特征：包含分隔符或 ~ 前缀
    if any(token in value for token in ("\\", "/", ":")) or value.startswith("~"):
        return project_key_from_path(value)
    return value


def trim_excerpt(text: str | None, limit: int = 220) -> str:
    """
    将文本压缩为不超过 `limit` 字符的单行摘要。
    超过则截断并附加 "..."。
    """
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[: limit - 3] + "..." if len(compact) > limit else compact


def contains_mojibake(text: str | None) -> bool:
    """
    检测文本是否包含编码乱码（GBK↔UTF-8 双重编码特征）。

    检测策略：
      1. 匹配 GBK→UTF-8 双重编码产生的常见乱码字符串
      2. 连续 4 个及以上问号（单字节编码丢失字符的典型特征）
      3. 汉字或英文字母后紧跟问号的混合乱码模式
    """
    if not text:
        return False
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return True
    if "????" in text or re.search(r"\?{4,}", text) is not None:
        return True
    if re.search(r"[A-Za-z]\?(?:[,.\s]|$)|[\u4e00-\u9fff]\?(?:[,.\s]|$)", text):
        return True
    return False


def ensure_parent(path: Path) -> None:
    """确保文件路径的父目录存在（如不存在则递归创建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)


def parse_timestamp(value: str | None) -> datetime | None:
    """
    将 ISO 格式时间戳字符串解析为 datetime 对象。

    处理多种格式：
    - 带 Z 后缀: "2024-01-15T10:30:00Z"
    - 带时区偏移: "2024-01-15T10:30:00+00:00"
    - 无时区: "2024-01-15T10:30:00"
    """
    if not value:
        return None
    value = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def time_ago(value: str | None) -> str:
    """
    将时间戳转换为中文相对时间描述。

    示例：2024-01-15T10:30:00+00:00 -> "3天前"
    """
    ts = value
    if not ts:
        return "未知"
    dt = parse_timestamp(ts)
    if dt is None:
        return "未知"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    days = (now - dt).days
    if days == 0:
        return "今天"
    elif days == 1:
        return "1天前"
    return f"{days}天前"


def load_records(
    store: MemoryStore,
    *,
    exclude_statuses: set[str] | None = None,
) -> list[MemoryRecord]:
    """
    从 store 加载记忆记录，支持按状态过滤。

    与 _load_active_records 和 _load_records 的区别：
    - 统一的接口，支持任意状态过滤
    - 自动跳过解析失败的记录
    """
    from ai_memory_hub.core.models import MemoryRecord

    exclude_statuses = exclude_statuses or set()
    records: list[MemoryRecord] = []
    for path in store.iter_memory_files():
        try:
            record = MemoryRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if record.status in exclude_statuses:
                continue
            records.append(record)
        except Exception:
            continue
    return records


def load_records_with_mtime(store: MemoryStore) -> list[tuple[MemoryRecord, Path, int]]:
    """
    从 store 加载记忆记录及其文件路径和修改时间。

    返回: list of (record, path, mtime_ns)
    """
    from ai_memory_hub.core.models import MemoryRecord

    result: list[tuple[MemoryRecord, Path, int]] = []
    for path in store.iter_memory_files():
        try:
            record = MemoryRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        result.append((record, path, mtime_ns))
    return result
