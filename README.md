# AI Memory Hub

> 本地优先的 AI 记忆中枢，为 Codex、Claude、Cursor、Gemini 等工具共享长期上下文

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**AI Memory Hub** 是一个本地优先的 AI 记忆中枢，将 Codex、Claude、Cursor、Gemini 等工具协作中的高价值经验，沉淀成可检索、可复用的长期上下文。

## 特性

- **多工具共享记忆** - 支持 Codex、Claude、Cursor、Gemini 等主流 AI 工具
- **自动提炼** - 从对话日志中自动识别偏好、规则和模式
- **混合搜索** - 结合 BM25 关键词与向量语义，精准召回相关记忆
- **MCP 集成** - 暴露 `memory_search`、`memory_context`、`memory_write` 等工具
- **本地优先** - 数据完全存储在本地，JSON 文件 + SQLite FTS
- **跨平台** - 支持 Windows、macOS、Linux

## 安装

### Windows

```powershell
# PowerShell 安装（推荐）
irm https://raw.githubusercontent.com/lovelihehe/ai-memory-hub/main/scripts/install.ps1 | iex

# 或手动安装
git clone https://github.com/lovelihehe/ai-memory-hub.git
cd ai-memory-hub
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

### macOS / Linux

```bash
# Shell 安装（推荐）
curl -sSL https://raw.githubusercontent.com/lovelihehe/ai-memory-hub/main/scripts/install.sh | bash

# 或手动安装
git clone https://github.com/lovelihehe/ai-memory-hub.git
cd ai-memory-hub
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 前置要求

- Python 3.11+
- Git

## 快速开始

```bash
# 初始化
ai-memory init

# 跑一次完整流水线
ai-memory pipeline

# 检查系统状态
ai-memory doctor

# 搜索记忆
ai-memory search --query "项目偏好"

# 生成任务上下文
ai-memory context --tool codex --task-type implementation --query "数据库修改"
```

## 命令参考

| 命令 | 说明 |
|------|------|
| `ai-memory init` | 初始化数据目录和配置文件 |
| `ai-memory pipeline` | 执行完整流水线（采集→提炼→索引） |
| `ai-memory collect` | 采集原始事件（仅采集） |
| `ai-memory consolidate` | 提炼候选记忆（仅提炼） |
| `ai-memory index` | 重建检索索引（仅索引） |
| `ai-memory obsidian-sync` | 同步记忆到 Obsidian Vault |
| `ai-memory search` | 搜索记忆（支持关键词/语义搜索） |
| `ai-memory list` | 列出记忆（支持按状态/工具/项目过滤） |
| `ai-memory show` | 查看单条记忆详情 |
| `ai-memory review` | 审核单条记忆（confirm/promote/demote/archive/contradict/merge） |
| `ai-memory review-batch` | 批量审核记忆（支持按置信度/年龄过滤） |
| `ai-memory context` | 生成任务上下文（供 AI 工具使用） |
| `ai-memory stats` | 查看统计信息（记忆数量、质量指标） |
| `ai-memory growth` | 成长趋势分析（按周/月/季度） |
| `ai-memory doctor` | 健康检查（配置/存储/MCP/索引链路） |
| `ai-memory repair-data` | 修复数据问题（保守修复，不改正常内容） |
| `ai-memory release-check` | 发布前检查（CLI 完整性/文档干净度） |
| `ai-memory run-mcp` | 启动 MCP 服务器（供 AI 客户端调用） |
| `ai-memory mcp-self-check` | MCP 握手自检 |
| `ai-memory install-tasks` | 安装定时任务（Windows 任务计划程序） |
| `ai-memory export` | 导出全部数据为 ZIP |
| `ai-memory import` | 从 ZIP 导入数据 |

## 配置

所有配置均写入 `AI_MEMORY_HOME/config.json`（首次运行后自动生成），不再依赖环境变量。

### 完整配置示例

```json
{
  "app_home": "~/ai-memory-hub",
  "data_home": "~/ai-memory",
  "sources": {
    "codex_sessions": "~/.codex/sessions",
    "codex_history": "~/.codex/history.jsonl",
    "claude_transcripts": "~/.claude/transcripts",
    "claude_history": "~/.claude/history.jsonl",
    "manual_notes": "~/ai-memory/notes",
    "manual_rules": "~/ai-memory/rules"
  },
  "llm": {
    "enabled": true,
    "api_key": "sk-...",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "timeout_seconds": 30
  },
  "scan": {
    "explicit_activation_keywords": [
      "不要", "别", "优先", "请用", "请使用",
      "以后", "默认", "必须", "需要", "记住", "注意",
      "should", "must", "prefer", "always", "never", "default"
    ],
    "sensitivity_patterns": [
      "sk-", "api_key", "access_token", "auth_token",
      "password", "secret", "私钥", "密钥", "令牌"
    ],
    "auto_activate_confidence": 0.78,
    "auto_activate_stability": 0.65,
    "max_render_items": 18,
    "hybrid_alpha": 0.4,
    "max_events_per_llm_call": 50,
    "llm_refinement": {
      "enabled": false,
      "temperature": 0.1,
      "max_output_items": 20,
      "fallback_to_regex": true
    }
  },
  "obsidian": {
    "enabled": true,
    "vault_root": "F:/aimemory/ob/成长沉淀",
    "inbox_dir": "00-收件箱",
    "projects_dir": "01-项目",
    "rules_dir": "02-长期规则",
    "reviews_dir": "03-阶段复盘",
    "archive_dir": "99-归档",
    "inbox_pending_after_days": 7,
    "direct_route_confidence": 0.75,
    "capture_roles": ["user", "assistant"]
  },
  "tools": [
    {
      "id": "codex",
      "label": "Codex",
      "family": "coding-agent",
      "enabled": true,
      "source_paths": {
        "sessions": "~/.codex/sessions",
        "history": "~/.codex/history.jsonl"
      },
      "render_targets": []
    },
    {
      "id": "claude",
      "label": "Claude",
      "family": "coding-agent",
      "enabled": true,
      "source_paths": {
        "transcripts": "~/.claude/transcripts",
        "history": "~/.claude/history.jsonl"
      },
      "render_targets": []
    },
    {
      "id": "gemini",
      "label": "Gemini",
      "family": "assistant",
      "enabled": true,
      "source_paths": {
        "history": "~/.gemini/history.jsonl",
        "logs": "~/.gemini/sessions"
      },
      "render_targets": []
    },
    {
      "id": "opencode",
      "label": "OpenCode",
      "family": "coding-agent",
      "enabled": true,
      "source_paths": {
        "history": "~/.opencode/history.jsonl",
        "logs": "~/.opencode/sessions"
      },
      "render_targets": []
    },
    {
      "id": "cursor",
      "label": "Cursor",
      "family": "coding-agent",
      "enabled": false,
      "source_paths": {
        "projects": ""
      },
      "render_targets": []
    }
  ]
}
```

### 配置项说明

#### LLM 配置 (`llm`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `false` | 是否启用 LLM 功能（精炼、标题生成、路由决策等） |
| `api_key` | string | `""` | API 密钥 |
| `base_url` | string | `"https://api.openai.com/v1"` | API 基础地址（支持 OpenAI 兼容接口） |
| `model` | string | `"gpt-4o-mini"` | 默认模型 |
| `timeout_seconds` | int | `30` | 请求超时时间（秒） |

> **提示**：支持任何兼容 OpenAI Chat Completions API 的后端，只需修改 `base_url` 和 `api_key`。

#### 扫描配置 (`scan`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `explicit_activation_keywords` | list[string] | 见示例 | 显式激活关键词，命中后自动提炼 |
| `sensitivity_patterns` | list[string] | 见示例 | 敏感信息匹配模式，命中后标记 |
| `auto_activate_confidence` | float | `0.78` | 自动激活置信度阈值 |
| `auto_activate_stability` | float | `0.65` | 自动激活稳定性阈值 |
| `max_render_items` | int | `18` | 最大渲染条目数 |
| `hybrid_alpha` | float | `0.4` | 混合搜索权重：BM25 占比（0.0-1.0） |
| `max_events_per_llm_call` | int | `50` | 每次 LLM 调用最大事件数 |

#### LLM 精炼配置 (`scan.llm_refinement`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `false` | 是否启用 LLM 精炼（默认使用正则兜底） |
| `temperature` | float | `0.1` | 生成温度 |
| `max_output_items` | int | `20` | 最大输出条目数 |
| `fallback_to_regex` | bool | `true` | LLM 失败时是否回退到正则 |

#### Obsidian 配置 (`obsidian`)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用 Obsidian 同步 |
| `vault_root` | string | 自动检测 | Vault 根目录 |
| `inbox_dir` | string | `"00-收件箱"` | 收件箱目录名 |
| `projects_dir` | string | `"01-项目"` | 项目目录名 |
| `rules_dir` | string | `"02-长期规则"` | 规则目录名 |
| `reviews_dir` | string | `"03-阶段复盘"` | 复盘目录名 |
| `archive_dir` | string | `"99-归档"` | 归档目录名 |
| `inbox_pending_after_days` | int | `7` | 收件箱待审天数 |
| `direct_route_confidence` | float | `0.75` | 直接路由置信度阈值 |
| `capture_roles` | list[string] | `["user", "assistant"]` | 捕获的角色类型 |

#### 工具配置 (`tools`)

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 工具唯一标识 |
| `label` | string | 显示名称 |
| `family` | string | 工具家族（`coding-agent` / `assistant`） |
| `enabled` | bool | 是否启用 |
| `source_paths` | dict[string, string] | 数据源路径映射 |
| `render_targets` | list[object] | 渲染目标（支持 Obsidian 同步） |

## 记忆类型

| 类型 | 用途 |
|------|------|
| `profile` | 稳定偏好（语言、风格、工具倾向）|
| `procedural` | 规则与流程 |
| `semantic` | 模式与通用经验 |
| `episodic` | 特定事件与案例 |

**状态**：`candidate`（候选待审核）→ `active`（正式启用）

## 数据目录

```
ai-memory/
├── profile/       # profile 类型记忆
├── procedures/    # procedural 类型记忆
├── episodes/      # episodic 类型记忆
├── patterns/      # semantic 类型记忆
├── projects/      # 项目级记忆
├── inbox/        # candidate 记忆
├── index/        # SQLite / 向量索引
├── rendered/     # Markdown 视图
├── notes/        # 手工笔记
└── rules/        # 手工规则
```

## 定时任务

通过 `ai-memory install-tasks` 安装后，系统会按设定间隔自动调用 `scripts/run-pipeline.py`，执行完整流水线（采集→提炼→索引），无需手动干预。

### Windows

```bash
ai-memory install-tasks --interval-minutes 60
```

### macOS / Linux

手动配置 cron（install-tasks 命令仅支持 Windows）：

```bash
crontab -e
# 添加：0 * * * * /path/to/.venv/bin/python /path/to/scripts/run-pipeline.py
```

### macOS launchd

创建 plist 并加载：

```bash
launchctl load ~/Library/LaunchAgents/com.aimemoryhub.pipeline.plist
```

## MCP 服务器

MCP 服务器通过 `scripts/run-mcp.py` 启动，供 MCP 客户端（Claude Desktop、Cursor 等）调用。

### 工作原理

MCP 客户端启动时，通过 `scripts/run-mcp.py` 自动拉起 MCP 服务器；之后两者通过 stdio 通信，客户端可动态调用 `memory_search`、`memory_context`、`memory_write` 等工具。

### 配置方法

在 MCP 客户端配置文件中添加 `scripts/run-mcp.py` 路径：

```json
{
  "mcpServers": {
    "ai-memory": {
      "command": "python",
      "args": ["scripts\\run-mcp.py"],
      "cwd": "C:\\path\\to\\ai-memory-hub"
    }
  }
}
```

配置后运行 `ai-memory mcp-self-check` 验证连接是否正常。

### CLI 与 MCP 的区别

| 方式 | 用途 |
|------|------|
| `ai-memory <命令>` | 手动操作（采集、搜索、审核、导出等） |
| MCP 服务器 | AI 客户端运行时自动调用，动态读写记忆 |

核心功能完全一致，CLI 可独立使用，MCP 只是让其他 AI 工具能直接调用这些能力。

## 开发

```bash
# 安装开发环境
pip install -e ".[dev]"

# 运行测试
python -m unittest discover -s tests -v

# 发布前检查
ai-memory doctor
ai-memory release-check
```

## 文档

- [FAQ](docs/FAQ.md) - 常见问题
- [排障指南](docs/TROUBLESHOOTING.md) - 问题排查

## Codex MCP

See [docs/CODEX_MCP.md](docs/CODEX_MCP.md) for Codex MCP setup and generated config examples.

## License

Apache-2.0 License - 详见 [LICENSE](LICENSE)
