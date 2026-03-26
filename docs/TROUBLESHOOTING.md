# 排障指南

## 排查顺序

先跑这三个命令：

```bash
ai-memory doctor
ai-memory stats
ai-memory repair-data
```

它们分别回答：

- 系统能不能正常工作
- 当前数据质量如何
- 是否存在脏数据需要修复

## 问题 1：pipeline 跑完没有任何结果

**现象**：`collect` 成功但数量是 0，`stats` 里的 `raw_event_count` 也是 0

**检查**：

1. `config.json` 里的 `source_paths` 是否存在
2. 来源文件是否是当前适配器支持的格式（JSONL）
3. 对应工具是否 `enabled: true`

**建议命令**：

```bash
ai-memory doctor
ai-memory pipeline
ai-memory stats
```

## 问题 2：有原始事件，但没有正式记忆

**现象**：`raw_event_count > 0` 但 `memory_count` 很低

**原因**：

- 提炼阈值没达到自动激活条件
- 文本里缺少触发词
- 数据更像一次性聊天，不像长期规则

**处理**：

```bash
ai-memory list --status candidate
ai-memory review --id <memory-id> --action confirm
```

如果长期提炼偏少，可以调整 `scan.explicit_activation_keywords`。

## 问题 3：搜索或 context 返回很空

**处理顺序**：

```bash
ai-memory doctor
ai-memory stats
ai-memory index
```

如果仍然异常：

```bash
ai-memory repair-data
ai-memory index
```

## 问题 4：MCP 连接失败

**现象**：MCP 客户端（Claude Desktop、Cursor）无法连接到 AI Memory Hub

**原因 1**：`scripts/run-mcp.py` 不存在或路径配置错误

**解决**：确认配置文件中的 `cwd` 指向 ai-memory-hub 仓库根目录，`args` 指向 `scripts\\run-mcp.py`：

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

**原因 2**：MCP 运行时依赖未安装

**解决**：

```bash
pip install "mcp>=1.12.4,<2"
```

**原因 3**：Python 环境找不到虚拟环境

**解决**：先确保已在仓库根目录运行 `pip install -e .` 以创建 `.venv`。

**验证连接**：

```bash
ai-memory mcp-self-check
```

## 问题 5：install-tasks 创建计划任务失败

**现象**：返回 `ok: false`，带 `manual_command`

**原因**：

- 账号没有创建计划任务的权限
- `.venv\Scripts\python.exe` 不存在
- `scripts/run-pipeline.cmd` 不存在

**处理**：

```bash
python -m venv .venv
pip install -e .
ai-memory install-tasks
```

如果仍失败，直接执行返回值里的 `manual_command`。

## 问题 6：stats 提示数据可能失真

**现象**：`stats_may_be_skewed: true`，有 `invalid_stability_memories` 等

**处理**：

```bash
ai-memory repair-data
ai-memory stats
ai-memory doctor
```

## 问题 7：记忆质量差

**现象**：候选太多，正式记忆混入一次性内容

**原因**：

- 触发词太宽
- 数据源噪音太多
- 缺少人工审核

**建议**：

1. 缩小 `explicit_activation_keywords`
2. 定期审查 `candidate`
3. 用 `notes/` 和 `rules/` 存明确规则

## 问题 8：语义搜索返回空

**原因**：

- 向量索引还未构建
- 语义模型加载失败
- 搜索查询过于模糊

**处理**：

1. 确保已运行 `pipeline` 构建向量索引
2. 检查日志中是否有模型加载错误
3. 尝试更具体的搜索查询

## 问题 9：导出/导入功能失败

**原因**：

- 权限不足
- 磁盘空间不够
- ZIP 文件损坏

**处理**：

1. 确保有足够磁盘空间
2. 确保有写入权限
3. 检查 ZIP 文件完整性

## 问题 10：LLM 功能不可用（精炼/标题生成/路由决策）

**现象**：启用了 `scan.llm_refinement.enabled: true`，但系统仍然使用正则提炼

**原因**：

- `llm.enabled` 未设为 `true`
- `llm.api_key`、`llm.base_url` 或 `llm.model` 为空
- 网络不通或 API 返回错误

**检查**：

1. 确认 `llm` 字段下四项必填项均已填写：

```json
"llm": {
  "enabled": true,
  "api_key": "sk-...",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini"
}
```

2. 确认网络可访问 API 端点

3. 确认 API 密钥有效且额度充足

**提示**：支持任何兼容 OpenAI Chat Completions API 的后端，只需修改 `base_url`。

## 问题 11：LLM 请求超时

**现象**：`llm.timeout_seconds` 过短导致调用失败

**处理**：在 `llm` 配置中增大超时时间：

```json
"llm": {
  "timeout_seconds": 60
}
```

建议值：本地模型 120 秒，云端 API 30-60 秒。

## 快速诊断

| 症状 | 命令 |
|------|------|
| 不知道系统是否健康 | `ai-memory doctor` |
| 不知道数据是否正常 | `ai-memory stats` |
| 怀疑数据坏了 | `ai-memory repair-data` |
| 怀疑索引没更新 | `ai-memory index` |
| 想重新跑全链路 | `ai-memory pipeline` |
| 记忆找不到 | `ai-memory search --query <关键词>` |
| 想批量审核候选 | `ai-memory review-batch --action confirm --dry-run` |
| MCP 连接失败 | `ai-memory mcp-self-check` |
| 导出数据备份 | `ai-memory export --output backup.zip` |
| 修复后验证 | `ai-memory repair-data && ai-memory doctor` |
