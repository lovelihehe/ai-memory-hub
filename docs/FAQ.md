# 常见问题

## 这是什么

本地优先的 AI 记忆系统。目标是保存真正值得长期复用的内容：

- 偏好
- 规则
- 项目决策
- 通用模式

## 和普通笔记软件有什么区别

普通笔记偏向人工整理知识。AI Memory Hub 偏向从 AI 协作日志里自动提炼高价值信息，变成可检索、可审核的结构化记忆。

## 和聊天历史有什么区别

聊天历史回答"发生过什么"。AI Memory Hub 回答"以后还值得继续带回来的是什么"。

## 为什么 JSON 做事实层、SQLite 做检索层

- JSON 文件可读、可迁移、可手工修改
- SQLite FTS 检索快，适合搜索与上下文召回

文件是真实来源，SQLite 是索引（不是唯一真相）。

## 为什么用 JSON 做事实层

JSON 可读、可迁移、可手工修改。SQLite FTS 用于快速检索，两者配合是最佳实践。

## 支持哪些工具

默认内置：Codex、Claude、Gemini、OpenCode、Cursor。也可以通过 `tools` 配置继续扩展。

## candidate 和 active 有什么区别

- `candidate` - 候选记忆，需要人工确认
- `active` - 正式启用，参与搜索和上下文

## shared 和分工具记忆是什么

记忆有一个 `tool` 字段：

- `shared` - 多工具共享，优先级最高
- 具体工具如 `codex` - 某工具特有的偏好

在 `context` 里，shared 记忆会优先返回。

## review 支持哪些动作

`confirm`、`promote`、`demote`、`archive`、`contradict`、`merge`。都会把记录标记为人工接管过的状态。

## MCP 是必须的吗

不是。核心能力通过 CLI 就能用。MCP 的作用是让其他 AI 客户端动态调用 `memory_search`、`memory_context`、`memory_write` 等工具。

## 手工 notes 和 rules 有什么用

系统会额外扫描 `AI_MEMORY_HOME/notes` 和 `AI_MEMORY_HOME/rules`，适合补充自动提炼不容易抓到的内容，如明确的团队规则、个人长期偏好。

## 为什么有时候搜索不到

常见原因：

1. 还没跑 `pipeline`
2. 来源路径配置错误
3. 还没有 `active` 记忆
4. 索引需要重建
5. 数据质量有问题

排查顺序：

```bash
ai-memory doctor
ai-memory stats
ai-memory pipeline
```

## Obsidian 同步怎么用

```bash
ai-memory obsidian-sync
```

将所有 active 记忆渲染为 Markdown 文件并同步到 Obsidian Vault。首次运行会自动创建目录结构。

## 什么是语义搜索

基于语义相似度的搜索，理解查询含义而非关键词匹配。

```bash
ai-memory search --query "如何优化代码性能" --semantic
```

适合概念性查询，自然语言问题、跨语言搜索。

## doctor 和 release-check 有什么区别

- `doctor` - 看运行健康（配置、存储、MCP、搜索链路、数据质量）
- `release-check` - 看发布质量（CLI 完整性、文档干净度）

## repair-data 会把数据修坏吗

当前实现偏保守。只修复：

- 超出范围的 `stability`
- 明显异常的时间字段

不会主动重写正常内容或改动记忆 ID。

## 什么是成长追踪

`ai-memory growth` 分析 active 记忆趋势，返回：

- 本周期新增多少条
- 哪些记忆被 AI 实际使用（`usage_count`）
- 是否达到里程碑（10 / 25 / 50 / 100 / 200 / 500 条）

## 批量 review 怎么用

```bash
ai-memory review-batch --action confirm --min-confidence 0.85
```

加上 `--dry-run` 可以先预览，不真正写入。

## LLM 提炼是什么

> **重要**：LLM 配置不再从环境变量读取，请直接在 `config.json` 的 `llm` 字段中配置。

默认关闭。开启后系统把原始事件批量送入 LLM，由 LLM 代替正则做记忆提炼。需要先在 `config.json` 中配置 `llm`：

```json
{
  "llm": {
    "enabled": true,
    "api_key": "sk-...",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "timeout_seconds": 30
  },
  "scan": {
    "llm_refinement": {
      "enabled": true
    }
  }
}
```

同时将 `scan.llm_refinement.enabled` 设为 `true` 即可启用。

**支持任何兼容 OpenAI Chat Completions API 的后端**，只需修改 `base_url`（如 LM Studio、Ollama、SiliconFlow 等）。关闭 LLM 精炼时使用正则表达式作为兜底。

## 混合搜索是什么意思

同时执行 BM25 关键词匹配和向量语义检索，按 `hybrid_alpha` 加权：

- `hybrid_alpha = 0.4` → BM25 权重 40%，向量权重 60%
- `--semantic` 标志临时提高向量权重
- 设为 `1.0` 退回纯关键词搜索

## 怎么导出和导入数据

```bash
# 导出全部数据（含记忆、索引、笔记、规则）
ai-memory export --output ~/ai-memory-backup.zip

# 导入数据（会合并，非覆盖）
ai-memory import --input ~/ai-memory-backup.zip
```

## 定时任务怎么配置

`install-tasks` 命令（仅 Windows）自动创建定时任务，定时调用 `scripts/run-pipeline.py` 执行完整流水线：

```bash
# Windows：每小时自动跑 pipeline
ai-memory install-tasks --interval-minutes 60
```

Linux/macOS 需手动配置 cron 或 launchd：

```bash
# cron 示例（每小时执行一次）
crontab -e
# 添加：0 * * * * /path/to/.venv/bin/python /path/to/scripts/run-pipeline.py
```

> `scripts/run-pipeline.py` 由定时器自动调用，用户无需手动运行。

## MCP 服务器怎么配置

### run-mcp.py 是什么

`scripts/run-mcp.py` 是 MCP 服务器的启动脚本，由 MCP 客户端（Claude Desktop、Cursor 等）调用。客户端启动时自动拉起该脚本，之后通过 stdio 协议与 MCP 服务器通信。

**用户不需要手动运行它**——只需要在 MCP 客户端的配置文件中引用即可。

### 配置步骤

**第一步**：在 MCP 客户端的配置文件中引用脚本路径：

Claude Desktop（`%APPDATA%\Claude\claude_desktop_config.json`）：

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

Cursor（`%APPDATA%\Cursor\User\globalStorage\saoudmeckami-mcp\settings.json`）配置方式相同。

**第二步**：验证连接：

```bash
ai-memory mcp-self-check
```

### CLI 和 MCP 的区别

- **CLI**（`ai-memory <命令>`）：手动操作，适合一次性任务
- **MCP 服务器**：AI 客户端运行时自动调用，适合让 AI 动态使用记忆

两者功能完全一致，MCP 只是让 AI 客户端能自动调用 CLI 的能力。
