"""
核心配置模块。

提供内存中枢的配置管理，包括：
- MemoryConfig: 主配置类
- SourceConfig: 数据源路径配置
- ToolConfig: 工具配置
- ScanConfig: 扫描参数配置
- ObsidianConfig: Obsidian 同步配置
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ai_memory_hub.core.utils import contains_mojibake


# 默认的显式激活关键词（用于识别用户偏好）
DEFAULT_EXPLICIT_ACTIVATION_KEYWORDS = [
    "不要", "别", "优先", "请用", "请使用",
    "以后", "默认", "必须", "需要", "记住", "注意",
    "should", "must", "prefer", "always", "never", "default",
]

# 默认的敏感信息匹配模式
DEFAULT_SENSITIVITY_PATTERNS = [
    "sk-", "api_key", "access_token", "auth_token",
    "password", "secret", "私钥", "密钥", "令牌",
]


def _home() -> Path:
    """获取用户主目录。"""
    return Path.home()


def _default_app_home() -> Path:
    """获取应用主目录，优先使用环境变量 AI_MEMORY_APP_HOME。"""
    return Path(os.getenv("AI_MEMORY_APP_HOME", _home() / "ai-memory-hub"))


def _default_data_home() -> Path:
    """获取数据目录，优先使用环境变量 AI_MEMORY_HOME。"""
    return Path(os.getenv("AI_MEMORY_HOME", _home() / "ai-memory"))


@dataclass(slots=True)
class SourceConfig:
    """数据源路径配置。"""
    codex_sessions: str           # Codex 会话目录
    codex_history: str            # Codex 历史记录文件
    claude_transcripts: str       # Claude 转录文件目录
    claude_history: str           # Claude 历史记录文件
    manual_notes: str             # 手动笔记目录
    manual_rules: str             # 手动规则目录


@dataclass(slots=True)
class ToolRenderTarget:
    """工具渲染目标配置。"""
    kind: str  # 目标类型（如 "obsidian"）
    path: str  # 目标路径


@dataclass(slots=True)
class ToolConfig:
    """工具配置。"""
    id: str                                          # 工具唯一标识
    label: str                                       # 工具显示名称
    family: str                                      # 工具家族（如 "coding-agent"）
    enabled: bool = True                             # 是否启用
    source_paths: dict[str, str] = field(default_factory=dict)  # 数据源路径映射
    render_targets: list[ToolRenderTarget] = field(default_factory=list)  # 渲染目标列表


@dataclass(slots=True)
class LlmRefinementConfig:
    """LLM 精炼配置。"""
    enabled: bool = False              # 是否启用 LLM 精炼
    temperature: float = 0.1           # 生成温度
    max_output_items: int = 20        # 最大输出条目数
    fallback_to_regex: bool = True    # LLM 失败时是否回退到正则


@dataclass(slots=True)
class LlmConfig:
    """LLM API 配置。直接从 config.json 读取，不再依赖环境变量。"""
    api_key: str = ""                  # API 密钥
    base_url: str = "https://api.openai.com/v1"   # API 基础地址
    model: str = "gpt-4o-mini"        # 默认模型
    timeout_seconds: int = 30          # 请求超时（秒）
    enabled: bool = False              # 是否启用 LLM 功能

    def is_complete(self) -> bool:
        """检查配置是否完整（api_key、base_url、model 都不为空）。"""
        return bool(self.api_key and self.base_url and self.model)


@dataclass(slots=True)
class ScanConfig:
    """扫描参数配置。"""
    explicit_activation_keywords: list[str] = field(
        default_factory=lambda: DEFAULT_EXPLICIT_ACTIVATION_KEYWORDS.copy()
    )   # 显式激活关键词
    sensitivity_patterns: list[str] = field(
        default_factory=lambda: DEFAULT_SENSITIVITY_PATTERNS.copy()
    )   # 敏感信息匹配模式
    auto_activate_confidence: float = 0.78      # 自动激活置信度阈值
    auto_activate_stability: float = 0.65       # 自动激活稳定性阈值
    max_render_items: int = 18                   # 最大渲染条目数
    llm_refinement: LlmRefinementConfig = field(
        default_factory=lambda: LlmRefinementConfig()
    )   # LLM 精炼配置
    hybrid_alpha: float = 0.4                   # 混合搜索 alpha 权重
    max_events_per_llm_call: int = 50           # 每次 LLM 调用最大事件数


@dataclass(slots=True)
class BootstrapProject:
    """引导项目配置。"""
    id: str = ""                                  # 项目标识
    name: str = ""                                 # 项目名称
    path: str = ""                                 # 项目路径
    tags: list[str] = field(default_factory=list) # 项目标签
    fact_templates: list[dict] = field(default_factory=list)  # 事实模板


@dataclass(slots=True)
class ObsidianConfig:
    """Obsidian 同步配置。"""
    enabled: bool = True                                      # 是否启用
    vault_root: str = ""                                       # Vault 根目录
    inbox_dir: str = "00-收件箱"                               # 收件箱目录
    projects_dir: str = "01-项目"                              # 项目目录
    rules_dir: str = "02-长期规则"                             # 规则目录
    reviews_dir: str = "03-阶段复盘"                           # 复盘目录
    archive_dir: str = "99-归档"                               # 归档目录
    inbox_pending_after_days: int = 7                          # 收件箱待审天数
    direct_route_confidence: float = 0.75                      # 直接路由置信度阈值
    capture_roles: list[str] = field(
        default_factory=lambda: ["user", "assistant"]
    )   # 捕获的角色类型


@dataclass(slots=True)
class MemoryConfig:
    """
    内存中枢主配置类。

    包含所有配置项，提供配置加载和保存功能。
    """
    app_home: str                              # 应用主目录
    data_home: str                             # 数据目录
    sources: SourceConfig                       # 数据源配置
    scan: ScanConfig                           # 扫描配置
    obsidian: ObsidianConfig                    # Obsidian 配置
    tools: list[ToolConfig]                     # 工具配置列表
    bootstrap_projects: list[BootstrapProject] = field(default_factory=list)  # 引导项目列表
    llm: LlmConfig = field(default_factory=lambda: LlmConfig())  # LLM API 配置

    @property
    def app_home_path(self) -> Path:
        """获取应用主目录路径。"""
        return Path(self.app_home)

    @property
    def data_home_path(self) -> Path:
        """获取数据目录路径。"""
        return Path(self.data_home)

    @property
    def config_path(self) -> Path:
        """获取配置文件路径。"""
        return self.data_home_path / "config.json"

    @property
    def enabled_tools(self) -> list[ToolConfig]:
        """获取所有已启用的工具列表。"""
        return [tool for tool in self.tools if tool.enabled]

    def get_tool(self, tool_id: str | None) -> ToolConfig | None:
        """根据工具 ID 获取工具配置。"""
        if not tool_id:
            return None
        for tool in self.tools:
            if tool.id == tool_id:
                return tool
        return None

    @staticmethod
    def default() -> "MemoryConfig":
        """
        创建默认配置。
        
        根据系统环境自动检测合理的数据源路径。
        """
        home = _home()
        data_home = _default_data_home()
        sources = SourceConfig(
            codex_sessions=str(home / ".codex" / "sessions"),
            codex_history=str(home / ".codex" / "history.jsonl"),
            claude_transcripts=str(home / ".claude" / "transcripts"),
            claude_history=str(home / ".claude" / "history.jsonl"),
            manual_notes=str(data_home / "notes"),
            manual_rules=str(data_home / "rules"),
        )
        return MemoryConfig(
            app_home=str(_default_app_home()),
            data_home=str(data_home),
            sources=sources,
            scan=ScanConfig(),
            obsidian=default_obsidian_config(data_home=data_home),
            tools=default_tools(home=home, data_home=data_home, sources=sources),
        )

    def ensure_written(self) -> None:
        """
        确保配置被写入磁盘。
        
        如果目录不存在会自动创建。
        """
        self.data_home_path.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )


def _sanitize_scan_strings(values: list[str] | None, defaults: list[str]) -> list[str]:
    """
    清理扫描字符串列表。
    
    移除重复项和乱码字符串。
    """
    merged: list[str] = []
    seen: set[str] = set()
    for value in defaults:
        lowered = value.lower()
        if lowered in seen:
            continue
        merged.append(value)
        seen.add(lowered)

    for item in values or []:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or contains_mojibake(value):
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        merged.append(value)
        seen.add(lowered)
    return merged


def _sanitize_llm_config(payload: dict | None, defaults: LlmRefinementConfig) -> LlmRefinementConfig:
    """从字典加载 LLM 配置，处理缺失值。"""
    payload = payload or {}
    return LlmRefinementConfig(
        enabled=bool(payload.get("enabled", defaults.enabled)),
        temperature=float(payload.get("temperature", defaults.temperature)),
        max_output_items=int(payload.get("max_output_items", defaults.max_output_items)),
        fallback_to_regex=bool(payload.get("fallback_to_regex", defaults.fallback_to_regex)),
    )


def _sanitize_api_llm(payload: dict | None, defaults: LlmConfig) -> LlmConfig:
    """从字典加载 LLM API 配置，处理缺失值。"""
    payload = payload or {}
    base_url = payload.get("base_url", defaults.base_url) or defaults.base_url
    if base_url:
        base_url = base_url.strip().rstrip("/")
    api_key = payload.get("api_key", defaults.api_key) or defaults.api_key
    return LlmConfig(
        api_key=api_key,
        base_url=base_url,
        model=str(payload.get("model", defaults.model) or defaults.model),
        timeout_seconds=int(payload.get("timeout_seconds", defaults.timeout_seconds)),
        enabled=bool(payload.get("enabled", defaults.enabled)),
    )


def _sanitize_scan_config(payload: dict | None, defaults: ScanConfig) -> ScanConfig:
    """从字典加载扫描配置，处理缺失值。"""
    payload = payload or {}
    llm_payload = payload.get("llm_refinement")
    hybrid_alpha = float(payload.get("hybrid_alpha", defaults.hybrid_alpha))
    hybrid_alpha = max(0.0, min(1.0, hybrid_alpha))
    max_events = int(payload.get("max_events_per_llm_call", defaults.max_events_per_llm_call))
    max_events = max(10, min(200, max_events))
    return ScanConfig(
        explicit_activation_keywords=_sanitize_scan_strings(
            payload.get("explicit_activation_keywords"),
            DEFAULT_EXPLICIT_ACTIVATION_KEYWORDS,
        ),
        sensitivity_patterns=_sanitize_scan_strings(
            payload.get("sensitivity_patterns"),
            DEFAULT_SENSITIVITY_PATTERNS,
        ),
        auto_activate_confidence=float(payload.get("auto_activate_confidence", defaults.auto_activate_confidence)),
        auto_activate_stability=float(payload.get("auto_activate_stability", defaults.auto_activate_stability)),
        max_render_items=int(payload.get("max_render_items", defaults.max_render_items)),
        llm_refinement=_sanitize_llm_config(llm_payload, defaults.llm_refinement),
        hybrid_alpha=hybrid_alpha,
        max_events_per_llm_call=max_events,
    )


def _load_bootstrap_projects(payload: list[dict] | None) -> list[BootstrapProject]:
    """从字典列表加载引导项目配置。"""
    if not payload:
        return []
    projects: list[BootstrapProject] = []
    for item in payload:
        projects.append(BootstrapProject(
            id=item["id"],
            name=item.get("name", item["id"]),
            path=item["path"],
            tags=list(item.get("tags", [])),
            fact_templates=list(item.get("fact_templates", [])),
        ))
    return projects


def load_config() -> MemoryConfig:
    """
    加载内存中枢配置。
    
    如果配置文件不存在，会创建默认配置并保存。
    加载时会验证并清理配置值。
    """
    default_config = MemoryConfig.default()
    path = default_config.config_path
    if not path.exists():
        default_config.ensure_written()
        return default_config
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources_payload = payload.get("sources") or asdict(default_config.sources)
    sources = SourceConfig(**sources_payload)
    config = MemoryConfig(
        app_home=payload["app_home"],
        data_home=payload["data_home"],
        sources=sources,
        scan=_sanitize_scan_config(payload.get("scan"), default_config.scan),
        obsidian=_load_obsidian_config(payload.get("obsidian"), default_config.obsidian),
        tools=_load_tools(
            payload.get("tools"),
            default_tools(home=_home(), data_home=Path(payload["data_home"]), sources=sources)
        ),
        bootstrap_projects=_load_bootstrap_projects(payload.get("bootstrap_projects")),
        llm=_sanitize_api_llm(payload.get("llm"), default_config.llm),
    )
    if payload != asdict(config):
        config.ensure_written()
    return config


def default_tools(*, home: Path, data_home: Path, sources: SourceConfig) -> list[ToolConfig]:
    """
    创建默认工具配置列表。
    
    支持 Codex、Claude、Gemini、OpenCode、Cursor 等工具。
    """
    return [
        ToolConfig(
            id="codex",
            label="Codex",
            family="coding-agent",
            source_paths={"sessions": sources.codex_sessions, "history": sources.codex_history},
        ),
        ToolConfig(
            id="claude",
            label="Claude",
            family="coding-agent",
            source_paths={"transcripts": sources.claude_transcripts, "history": sources.claude_history},
        ),
        ToolConfig(
            id="gemini",
            label="Gemini",
            family="assistant",
            source_paths={
                "history": str(home / ".gemini" / "history.jsonl"),
                "logs": str(home / ".gemini" / "sessions"),
            },
            render_targets=[],
        ),
        ToolConfig(
            id="opencode",
            label="OpenCode",
            family="coding-agent",
            source_paths={
                "history": str(home / ".opencode" / "history.jsonl"),
                "logs": str(home / ".opencode" / "sessions"),
            },
            render_targets=[],
        ),
        ToolConfig(
            id="cursor",
            label="Cursor",
            family="coding-agent",
            enabled=False,
            source_paths={"projects": ""},
            render_targets=[],
        ),
    ]


def _default_obsidian_root(data_home: Path) -> Path:
    """获取默认的 Obsidian Vault 根目录。"""
    preferred = Path(r"F:\aimemory\ob\成长沉淀")
    if preferred.exists():
        return preferred
    return data_home / "obsidian-growth"


def default_obsidian_config(*, data_home: Path) -> ObsidianConfig:
    """创建默认的 Obsidian 配置。"""
    return ObsidianConfig(vault_root=str(_default_obsidian_root(data_home)))


def _load_obsidian_config(payload: dict | None, defaults: ObsidianConfig) -> ObsidianConfig:
    """从字典加载 Obsidian 配置。"""
    payload = payload or {}
    capture_roles = payload.get("capture_roles") or defaults.capture_roles
    valid_roles = [
        role for role in capture_roles
        if isinstance(role, str) and role in {"user", "assistant", "developer", "system"}
    ]
    return ObsidianConfig(
        enabled=bool(payload.get("enabled", defaults.enabled)),
        vault_root=str(payload.get("vault_root") or defaults.vault_root),
        inbox_dir=str(payload.get("inbox_dir", defaults.inbox_dir)),
        projects_dir=str(payload.get("projects_dir", defaults.projects_dir)),
        rules_dir=str(payload.get("rules_dir", defaults.rules_dir)),
        reviews_dir=str(payload.get("reviews_dir", defaults.reviews_dir)),
        archive_dir=str(payload.get("archive_dir", defaults.archive_dir)),
        inbox_pending_after_days=int(payload.get("inbox_pending_after_days", defaults.inbox_pending_after_days)),
        direct_route_confidence=float(payload.get("direct_route_confidence", defaults.direct_route_confidence)),
        capture_roles=valid_roles or defaults.capture_roles,
    )


def _load_tools(payload: list[dict] | None, fallback: list[ToolConfig]) -> list[ToolConfig]:
    """
    从字典列表加载工具配置。
    
    如果 payload 为空或某些工具缺失，使用 fallback 中的默认值补充。
    """
    if not payload:
        return fallback
    tools: list[ToolConfig] = []
    seen_ids: set[str] = set()
    for item in payload:
        render_targets = [ToolRenderTarget(**target) for target in item.get("render_targets", [])]
        tool = ToolConfig(
            id=item["id"],
            label=item.get("label", item["id"].title()),
            family=item.get("family", item.get("kind", "assistant")),
            enabled=bool(item.get("enabled", True)),
            source_paths=dict(item.get("source_paths", {})),
            render_targets=render_targets,
        )
        tools.append(tool)
        seen_ids.add(tool.id)
    for tool in fallback:
        if tool.id not in seen_ids:
            tools.append(tool)
    return tools
