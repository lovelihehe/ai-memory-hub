# AI Memory Hub 架构文档

> 本文档系统阐述 AI Memory Hub 的整体架构设计、数据模型、核心流程与技术选型。

---

## 一、项目定位与目标

**AI Memory Hub** 是一个**本地优先的 AI 记忆中枢**，旨在解决多工具协作场景下的上下文遗忘问题：

- **问题**：Codex、Claude、Cursor、Gemini 等 AI 工具在每次会话后，用户的偏好、规则、模式等高价值信息随之消失。
- **解决**：将这些信息从会话日志中提取、沉淀为可检索的"记忆"，再通过 MCP 协议实时传递给 AI 工具。

**核心价值**：让 AI 工具在每次任务中，都能自动获取"必须遵循的规则"和"已知有效的模式"，避免重复沟通和遗忘。

---

## 二、整体架构

### 2.1 模块层次

```
┌─────────────────────────────────────────────────────────┐
│                    CLI 层 (cli.py)                       │
│         init / pipeline / search / context / review     │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Pipeline 层 (pipeline.py)                    │
│   collect → repair → consolidate → govern → index        │
│          → render → wiki → obsidian sync                │
└──────┬─────────────┬─────────────┬──────────────┬───────┘
       │             │             │              │
┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼──────┐ ┌─────▼──────┐
│  Extraction │ │  Storage  │ │  Services  │ │ Integrations│
│  数据抽取   │ │   存储    │ │  业务服务   │ │  外部集成  │
│  sources.py │ │  db.py    │ │ search.py  │ │mcp_server  │
│extractors.py│ │vector.py  │ │ manage.py  │ │obsidian.py │
│  quality.py │ │           │ │  wiki.py   │ │ scheduler  │
│llm_analysis │ │           │ │  stats.py  │ │  doctor    │
│             │ │           │ │  render.py │ │client_config│
└─────────────┘ └───────────┘ └────────────┘ └────────────┘
                       │
              ┌────────▼────────┐
              │   Core 层       │
              │ config / models │
              │  logger / utils │
              └─────────────────┘
```

### 2.2 核心数据流

```
外部工具会话文件
    │
    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ data sources │────▶│  RawEvent    │────▶│ consolidation│
│ (sources.py) │     │  原始事件    │     │ (extractors) │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                   │
                                          ┌────────▼────────┐
                                          │ CandidateMemory │
                                          │   候选记忆       │
                                          └────────┬────────┘
                                                   │
                                          ┌────────▼────────┐
                                          │ governance       │
                                          │  (quality.py)    │
                                          └────────┬────────┘
                                                   │
┌──────────────┐     ┌──────────────┐     ┌────────▼────────┐
│ Obsidian     │◀────│  Markdown    │◀────│ MemoryRecord    │
│ Vault Sync   │     │  Render      │     │  正式记忆        │
└──────┬───────┘     └──────────────┘     └────────┬────────┘
       │                                          │
       │              ┌──────────────┐            │
       └─────────────▶│ SQLite FTS5  │◀───────────┘
                      │ BM25 全文索引 │
                      └──────┬───────┘
                             │
                      ┌──────▼───────┐     ┌──────────────┐
                      │  ChromaDB     │────▶│  混合搜索    │
                      │  向量语义检索  │     │  (search.py) │
                      └───────────────┘     └──────┬───────┘
                                                    │
                                           ┌────────▼────────┐
                                           │ MCP Server      │
                                           │ memory_search   │
                                           │ memory_context  │
                                           └─────────────────┘
```

---

## 三、核心数据模型

### 3.1 数据类定义

全部定义在 `core/models.py`，使用 `@dataclass(slots=True)` 实现，节省内存。

#### RawEvent（原始事件）

```python
@dataclass(slots=True)
class RawEvent:
    id: str                          # 唯一标识（stable_id 哈希）
    source_tool: str                 # 来源工具（codex / claude / cursor / gemini / manual）
    source_path: str                 # 来源文件路径
    session_id: str | None          # 会话 ID
    event_type: str                 # 事件类型（message / tool_use / history_user_text）
    timestamp: str | None            # ISO 时间戳
    role: str | None                # 角色（user / assistant / developer / system）
    cwd: str | None                  # 工作目录（用于推导 project_key）
    project_key: str | None          # 项目键（从 cwd 推导）
    text: str | None                 # 消息文本
    command: str | None              # 命令（tool_use 类型事件专用）
    raw_json: str                    # 原始 JSON（保留原始数据）
```

#### Evidence（证据）

```python
@dataclass(slots=True)
class Evidence:
    source_tool: str                 # 来源工具
    source_path: str                 # 来源路径
    session_id: str | None          # 会话 ID
    timestamp: str | None           # 时间戳
    excerpt: str                     # 摘录文本（敏感信息已脱敏）
```

#### MemoryRecord（记忆记录）

```python
@dataclass(slots=True)
class MemoryRecord:
    id: str                          # 唯一标识（stable_id 哈希）
    title: str                       # 标题
    memory_type: str                 # 记忆类型
    scope: str                       # 范围
    tool: str                        # 来源工具
    project_key: str | None          # 项目键
    summary: str                    # 摘要
    details: str                     # 详细说明
    evidence: list[Evidence]        # 证据列表
    confidence: float                # 置信度 0.0-1.0
    stability: float                 # 稳定性 0.0-1.0
    sensitivity: str                  # 敏感度（high / low）
    tags: list[str]                  # 标签
    created_at: str                  # 创建时间
    last_seen_at: str | None        # 最后出现时间
    reviewed_at: str | None          # 审核时间
    status: str                      # 状态
    supersedes: str | None          # 替代关系（被哪条记忆替代）
    managed_by: str = "system"       # 管理方（system / llm / user）
    manual_override: bool = False    # 手动覆盖标记（防止 LLM 自动修改）
    last_accessed_at: str | None    # 最后访问时间（用于使用反馈）
    expiration_days: int = 90        # 过期天数
```

### 3.2 记忆类型系统

| 类型 | 标识 | 含义 | 典型示例 |
|------|------|------|----------|
| **profile** | `profile` | 稳定偏好 | 语言风格、工具倾向 |
| **procedural** | `procedures` | 规则与流程 | 编码规范、工作方式约定 |
| **semantic** | `patterns` | 模式与经验 | 技术选型、架构模式 |
| **episodic** | `episodes` | 特定案例 | 成功/失败案例、踩坑记录 |

### 3.3 记忆状态机

```
                    ┌──────────────┐
  candidate ──[自动激活]──▶ active
      │             (conf≥0.78  │
      │              stab≥0.65) │
      │                         │
      │    ┌────────────────────┤
      ▼    ▼                    ▼
  ┌──────────────┐        ┌──────────┐
  │  archived    │◀──[归档/去重]──┘
  └──────────────┘
      │
      │ (merge)
      ▼
  ┌──────────────┐
  │ contradicted │──[矛盾检测]──▶ (打 potential-conflict 标签)
  └──────────────┘
```

### 3.4 置信度与稳定性机制

**初始值**由记忆类型和显式程度决定：

| 记忆类型 | 初始置信度 | 初始稳定性 |
|----------|-----------|------------|
| rule / procedure | 0.55 + 0.18 = 0.73 | 0.80 |
| preference / watchout / fact | 0.55 + 0.10 = 0.65 | 0.65 |
| 默认 | 0.55 | 0.65 |

**叠加机制**：
- 重复出现（正则模式）：置信度 +0.08/次，稳定性 +0.07/次
- 上限：均不超过 0.99

**自动激活条件**（需同时满足）：
- 置信度 ≥ `auto_activate_confidence`（默认 0.78）
- 稳定性 ≥ `auto_activate_stability`（默认 0.65）
- scope ≠ `project`（项目级记忆需人工审核）
- memory_type ≠ `profile`（偏好类记忆需人工审核）
- 无 `needs-review` 标签

---

## 四、数据采集层

### 4.1 数据源解析（sources.py）

支持五大工具的数据解析，自动发现并增量采集：

| 工具 | 数据格式 | 会话路径 | 历史路径 |
|------|---------|---------|---------|
| **Codex** | JSONL | `~/.codex/sessions/` | `~/.codex/history.jsonl` |
| **Claude** | JSONL | `~/.claude/transcripts/` | `~/.claude/history.jsonl` |
| **Gemini** | JSONL | `~/.gemini/sessions/` | `~/.gemini/history.jsonl` |
| **OpenCode** | JSONL | `~/.opencode/sessions/` | `~/.opencode/history.jsonl` |
| **Cursor** | JSONL | `~/.cursor/projects/*/agent-transcripts/` | — |
| **Manual** | MD/TXT | `~/ai-memory/notes/` | `~/ai-memory/rules/` |

**增量采集机制**：
- 通过 `source_cursors` 表记录每个文件的处理位置（字节偏移）
- 文件大小未增长时，从上次位置继续读取
- 文件被截断时（大小减小），从头重新扫描
- 手工笔记（MD/TXT）每次全量读取

### 4.2 事件类型

| 类型 | 含义 | 角色 |
|------|------|------|
| `message` | 普通消息 | user / assistant |
| `tool_use` | 工具调用 | assistant |
| `history_user_text` | 历史记录文本 | user |
| `manual_note` | 手工笔记 | user |

---

## 五、记忆提炼层

### 5.1 两种提炼模式

#### 模式一：LLM 智能提炼

当 `config.scan.llm_refinement.enabled=true` 时启用：

```
批量原始事件（user 角色）
    │
    ▼
extract_memories_from_events()
(调用 OpenAI Chat Completions API)
    │
    ▼
候选记忆列表（带置信度/稳定性）
    │
    ▼
写入记忆库（candidate 状态）
    │
    ▼
自动激活（置信度 ≥ 阈值时直接 active）
```

**提示词设计**：从事件中提取偏好、规则、模式，并给出置信度和稳定性评分。

#### 模式二：正则匹配（默认兜底）

基于显式激活关键词的正则匹配，无需 LLM：

```python
PREFERENCE_PATTERNS = [
    (r"^(不要|别|never)\s*(.+)$", "watchout"),
    (r"^(优先|prefer)\s*(.+)$", "preference"),
    (r"^(请用|请使用|use)\s*(.+)$", "procedure"),
    (r"^(以后|默认|必须|需要|记住|always|must|default)\s*(.+)$", "rule"),
]
```

**低价值文本过滤**：

| 过滤规则 | 说明 |
|---------|------|
| `PLAN_MARKERS` | "please implement this plan" 等计划类标记 |
| `LOW_VALUE_MARKERS` | skill 引用、模板类文本 |
| `TASK_REQUEST_MARKERS` | "帮我"、"请帮我" 等请求类标记 |
| `PATH_ONLY_PATTERN` | 纯路径文本（≥2 个路径分隔符） |
| `CODE_FRAGMENT_PATTERN` | 含代码片段的文本 |
| 标题过短（<18字符）且无显式关键词 | |

### 5.2 记忆路由决策

提炼后的候选记忆根据置信度和内容特征路由到不同位置：

| 置信度阈值 | 路由位置 | 说明 |
|-----------|---------|------|
| ≥ 0.75 | 直接写入对应目录 | 无需审核 |
| < 0.75 | `inbox/` | 候选状态，待审核 |
| project_key 且含项目关键词 | `projects/{key}/memories/` | 项目级记忆 |

---

## 六、数据治理层

### 6.1 候选治理（govern_candidates）

自动处理候选记忆队列：

| 动作 | 触发条件 |
|------|---------|
| **自动激活** | 置信度≥阈值 且 稳定性≥阈值 且 非项目级 且 非偏好类 且 无 needs-review 标签 |
| **自动归档** | 低价值文本 或 重复记忆（保留置信度最高者） |
| **标记待审** | 项目级记忆 / 偏好类记忆 / 含 needs-review 标签 |

### 6.2 数据修复（repair_data）

| 问题类型 | 处理方式 |
|---------|---------|
| **乱码** | 系统生成→直接删除；手工记忆→标记 archived |
| **时间戳不规范** | 统一转换为 ISO 格式 |
| **稳定性越界** | clamp 到 [0, 1] |
| **证据过期** | 保留最近 12 条证据 |

### 6.3 矛盾检测

对同类型、同范围的两条 active 记忆，调用 LLM 检测是否矛盾。发现矛盾时，打上 `potential-conflict` 标签，由人工审核。

### 6.4 里程碑追踪

当 active 记忆数量达到 10/25/50/100/200/500 时，自动在 Obsidian 中创建里程碑笔记。

---

## 七、存储层

### 7.1 存储架构

```
ai-memory/                          # 数据根目录（~ /ai-memory）
├── profile/                       # profile 类型记忆（JSON 文件）
├── procedures/                   # procedural 类型记忆
├── episodes/                     # episodic 类型记忆
├── patterns/                     # semantic 类型记忆
├── projects/                     # 项目级记忆
│   └── {project_key}/
│       └── memories/
├── inbox/                        # candidate 记忆（待审核）
├── index/                        # 索引目录
│   ├── memory.db                 # SQLite 数据库
│   │   ├── memories 表          # 记忆索引（SQL 列）
│   │   ├── memory_fts 表        # FTS5 全文索引
│   │   ├── raw_events 表        # 原始事件
│   │   └── source_cursors 表   # 采集游标
│   └── vector/                   # ChromaDB 向量存储
├── rendered/                     # Markdown 渲染输出
├── wiki/                         # Wiki 生成
├── state/                        # 状态文件
│   └── milestones.json          # 里程碑状态
├── notes/                        # 手工笔记
└── rules/                        # 手工规则
```

### 7.2 SQLite 表结构

#### memories 表（记忆索引）

```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    tool TEXT NOT NULL,
    project_key TEXT,
    summary TEXT NOT NULL,
    details TEXT NOT NULL,
    evidence_json TEXT NOT NULL,   -- JSON 序列化 evidence 列表
    confidence REAL NOT NULL,
    stability REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    tags_json TEXT NOT NULL,         -- JSON 序列化标签列表
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    reviewed_at TEXT,
    status TEXT NOT NULL,
    supersedes TEXT,
    file_path TEXT NOT NULL,         -- JSON 文件路径
    managed_by TEXT NOT NULL DEFAULT 'system',
    manual_override INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TEXT,
    expiration_days INTEGER NOT NULL DEFAULT 90,
    usage_count INTEGER NOT NULL DEFAULT 0
);
```

#### memory_fts 表（FTS5 全文索引）

```sql
CREATE VIRTUAL TABLE memory_fts USING fts5(
    id, title, summary, details, tags,
    tokenize = 'unicode61'          -- Unicode 分词
);
```

检索评分使用 BM25 算法（`bm25(memory_fts, 4.0, 3.0, 1.5, 1.0)`），各字段权重：摘要 4.0、标题 3.0、标签 1.5、详情 1.0。

#### raw_events 表（原始事件）

```sql
CREATE TABLE raw_events (
    id TEXT PRIMARY KEY,
    source_tool TEXT NOT NULL,
    source_path TEXT NOT NULL,
    session_id TEXT,
    event_type TEXT NOT NULL,
    timestamp TEXT,
    role TEXT,
    cwd TEXT,
    project_key TEXT,
    text TEXT,
    command TEXT,
    raw_json TEXT NOT NULL
);
```

#### source_cursors 表（采集游标）

```sql
CREATE TABLE source_cursors (
    source_path TEXT PRIMARY KEY,
    file_size INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    modified_at TEXT
);
```

### 7.3 ChromaDB 向量存储

- **模型**：`all-MiniLM-L6-v2`（轻量高效的句子嵌入模型）
- **索引算法**：HNSW（`hnsw:space: cosine`），余弦距离
- **嵌入内容**：`{title} {summary} {details}`
- **懒加载**：仅当 `AI_MEMORY_ENABLE_VECTOR=1` 时启用，初始化失败时优雅降级
- **持久化**：存储在 `index/vector/` 目录

---

## 八、检索层

### 8.1 混合搜索算法

```
用户查询
  │
  ├─▶ FTS5 BM25 检索 ─────┐
  │   (全文精确匹配)       │
  │                       │  α × BM25 + (1-α) × 余弦相似度
  └─▶ ChromaDB 向量检索 ──┤
      (语义相似度)         │
                            ▼
                    加权融合排序
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
          项目级+active  全局shared+active  其他
          (最高优先)    (次高优先)       (最低)
```

**关键参数**：
- `hybrid_alpha`（默认 0.4）：BM25 权重。α=0.4 意味着 40% BM25 + 60% 向量
- `--semantic` 标志：将 α 降低 0.2，优先向量语义
- 检索上限：`limit * 3`（过召回后裁剪）

### 8.2 搜索优先级

```python
priority_tuple = (
    0,  # 项目级 active（最高）
    1,  # 全局 shared active
    2,  # 工具专属 active
    3,  # 项目级 candidate
    4,  # 其他（最低）
)
# 同优先级内，按置信度倒序、稳定性倒序、标题字母序
```

### 8.3 任务上下文生成

`memory_context()` 为 AI 工具生成结构化的任务上下文：

```python
{
    "must_follow": [procedural记忆, confidence≥0.82],   # 必须遵循
    "preferences": [profile记忆],                     # 偏好
    "known_patterns": [semantic记忆],                 # 已知模式
    "watch_outs": [含watchout标签的记忆],              # 注意事项
    "related_episodes": [episodic记忆],               # 相关案例
}
```

---

## 九、服务层

### 9.1 服务职责总览

| 服务 | 文件 | 职责 |
|------|------|------|
| 搜索 | `services/search.py` | 混合检索、上下文生成 |
| 管理 | `services/manage.py` | CRUD、审核反馈、批量操作 |
| Obsidian 同步 | `services/obsidian.py` | Vault 双向同步、自动归档、周报生成 |
| Wiki 生成 | `services/wiki.py` | 生成可浏览的 Wiki 页面 |
| 渲染 | `services/render.py` | Markdown 输出渲染 |
| 统计 | `services/stats.py` | 统计报告、成长趋势 |

### 9.2 审核反馈操作

| 操作 | 效果 |
|------|------|
| `promote` / `confirm` | candidate → active，置信度≥0.9，稳定性≥0.85 |
| `demote` | active → candidate |
| `archive` | 任意状态 → archived |
| `contradict` | 标记为矛盾，关联目标记忆 |
| `merge` | 归档当前记忆，关联目标记忆为 supersedes |

### 9.3 Obsidian 同步策略

```
候选记忆 ──[置信度≥0.75]──▶ inbox_dir（待审核）
                    │
                    ├──[profile/procedural]──▶ rules_dir
                    ├──[projects]──▶ projects_dir
                    └──[semantic/episodic]──▶ reviews_dir
                                         │
                            inbox_pending_after_days天后
                                         │
                                         ▼
                               自动迁移到对应目录
```

---

## 十、流水线编排

### 10.1 完整流水线（pipeline.py）

```python
run_pipeline():
    1. cleanup_expired_memories()    清理过期记忆
    2. collect_sources()             采集原始事件
    3. repair_data()                 修复数据问题
    4. consolidate()                提炼候选记忆
    5. bootstrap_known_projects()    引导已知项目
    6. govern_candidates()          治理候选记忆
    7. rebuild_memory_index()       重建索引（完整重建）
    8. render_outputs()              渲染 Markdown 输出
    9. build_wiki()                 生成 Wiki
    10. sync_obsidian_vault()       同步 Obsidian
```

各阶段独立可用：
- `run_collect()`：仅采集
- `run_consolidate()`：仅提炼
- `run_index()`：仅索引+渲染+Wiki+Obsidian

### 10.2 增量索引策略

索引重建支持增量模式：
- **完整重建**（`incremental=False`）：清空后重新写入所有文件
- **增量更新**（`incremental=True`）：只处理变化的文件，删除已消失的文件

---

## 十一、MCP 服务器集成

### 11.1 协议通信

通过 stdio 与 MCP 客户端通信，支持两种协议模式：
- **Content-Length 模式**：标准 MCP 协议
- **JSONL 模式**：逐行 JSON（兼容旧版）

### 11.2 暴露工具

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `memory_search` | 搜索记忆 | query, scope, tags, project, tool, limit |
| `memory_get` | 获取单条记忆 | memory_id |
| `memory_write` | 写入新记忆 | title, memory_type, scope, summary, details... |
| `memory_context` | 生成任务上下文 | tool, repo, task_type, query |
| `memory_feedback` | 应用反馈 | memory_id, action, target_id |
| `memory_refresh` | 触发流水线 | 无 |

### 11.3 自检机制

`mcp-self-check` 命令通过实际启动 MCP 服务器并发送 initialize 请求，验证连接是否正常。

---

## 十二、配置管理

### 12.1 配置存储

- **路径**：`AI_MEMORY_HOME/config.json`（`~/ai-memory/config.json`）
- **加载时机**：首次运行时自动创建默认配置
- **回退策略**：缺失字段使用默认值，不破坏现有配置

### 12.2 关键配置项

| 配置节 | 关键参数 | 默认值 | 说明 |
|--------|---------|--------|------|
| `llm` | `enabled` | false | 是否启用 LLM 精炼 |
| `llm` | `model` | gpt-4o-mini | LLM 模型 |
| `llm` | `base_url` | OpenAI API | 支持兼容接口 |
| `scan` | `auto_activate_confidence` | 0.78 | 自动激活置信度阈值 |
| `scan` | `auto_activate_stability` | 0.65 | 自动激活稳定性阈值 |
| `scan` | `hybrid_alpha` | 0.4 | 混合搜索 BM25 权重 |
| `scan` | `explicit_activation_keywords` | 16 个关键词 | 显式激活关键词列表 |
| `scan` | `sensitivity_patterns` | 10 个模式 | 敏感信息检测 |
| `obsidian` | `enabled` | true | 是否启用 Obsidian 同步 |
| `obsidian` | `vault_root` | 自动检测 | Vault 根目录 |

---

## 十三、技术选型

| 领域 | 选型 | 版本 | 说明 |
|------|------|------|------|
| **向量检索** | ChromaDB | ≥0.4.24 | 本地持久化向量数据库 |
| **嵌入模型** | all-MiniLM-L6-v2 | ≥3.0.0 | 384 维轻量嵌入，速度快 |
| **全文检索** | SQLite FTS5 | 内置 | BM25 评分，无需额外服务 |
| **LLM 接口** | OpenAI Chat Completions API | — | 支持兼容接口的自托管模型 |
| **MCP 协议** | mcp Python SDK | ≥1.12.4 | AI 工具集成标准 |
| **定时任务** | Windows Task Scheduler / cron | — | 自动流水线执行 |

---

## 十四、关键设计决策

### 14.1 本地优先

- 所有数据存储在本地文件系统，无云依赖
- JSON 文件格式便于调试和版本控制
- 可通过 `export`/`import` 命令进行备份和迁移

### 14.2 优雅降级

- 向量存储初始化失败时，自动降级为纯 FTS5 搜索
- LLM 精炼不可用时，自动回退为正则匹配
- 搜索结果为空时，返回按优先级排序的全部可用记忆

### 14.3 手动覆盖保护

- 用户审核过的记忆（`manual_override=True`）不会被 LLM 自动修改
- 保留 `reviewed_at` 时间戳，记录人工审核时机

### 14.4 增量处理

- 采集层通过游标实现增量，避免重复处理
- 索引重建支持增量更新，减少 I/O 开销

---

## 十五、目录结构

```
ai-memory-hub/
├── src/ai_memory_hub/
│   ├── __init__.py
│   ├── cli.py                   # CLI 入口
│   ├── core/
│   │   ├── config.py           # 配置管理
│   │   ├── models.py           # 数据模型（MemoryRecord / RawEvent）
│   │   ├── logger.py           # 日志
│   │   └── utils.py            # 工具函数（stable_id / trim_excerpt）
│   ├── storage/
│   │   ├── db.py               # SQLite + FTS5 存储
│   │   └── vector.py           # ChromaDB 向量存储
│   ├── extraction/
│   │   ├── sources.py          # 多工具数据源解析
│   │   ├── extractors.py       # 记忆提炼（正则 + LLM）
│   │   ├── quality.py          # 数据质量治理
│   │   └── llm_analysis.py     # LLM 辅助分析
│   ├── pipeline/
│   │   ├── pipeline.py         # 流水线编排
│   │   ├── bootstrap.py        # 项目引导
│   │   └── growth.py           # 成长趋势分析
│   ├── services/
│   │   ├── search.py           # 混合搜索
│   │   ├── manage.py           # 记忆管理
│   │   ├── obsidian.py         # Obsidian 同步
│   │   ├── wiki.py            # Wiki 生成
│   │   ├── stats.py           # 统计分析
│   │   └── render.py          # Markdown 渲染
│   └── integrations/
│       ├── mcp_server.py        # MCP 服务器
│       ├── client_config.py    # MCP 客户端配置生成
│       ├── doctor.py           # 健康检查
│       ├── scheduler.py        # 定时任务
│       └── release.py          # 发布检查
├── scripts/
│   ├── run-pipeline.py        # 流水线脚本入口
│   └── run-mcp.py             # MCP 服务器脚本入口
├── tests/
├── docs/
│   ├── FAQ.md
│   └── TROUBLESHOOTING.md
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## 附录 A：SQLite FTS5 BM25 权重

```sql
bm25(memory_fts, 4.0, 3.0, 1.5, 1.0)
--                  │     │     │     │
--                  │     │     │     └── details 权重 1.0
--                  │     │     └──────── tags 权重 1.5
--                  │     └────────────── title 权重 3.0
--                  └──────────────────── summary 权重 4.0
```

## 附录 B：显式激活关键词

**规则类**：`不要`、`别`、`优先`、`请用`、`请使用`、`以后`、`默认`、`必须`、`需要`、`记住`、`注意`、`should`、`must`、`prefer`、`always`、`never`、`default`

**偏好类**：`中文`、`english`、`tone`、`语气`、`输出`、`列表`、`bullets`、`markdown`、`格式`

**流程类**：`sql`、`数据库`、`test`、`测试`、`commit`、`提交`、`api`、`controller`、`service`、`mcp`、`脚本`、`sync`、`maven`、`build`、`profile`、`运行`、`启动`

**项目类**：`repo`、`repository`、`模块`、`module`、`pom.xml`、`maven`、`spring boot`、`controller`、`service`、`table`、`sql`、`数据库`、`profile`、`build`、`test`

## 附录 C：文件命名规范

| 记忆类型 | 目录 | 文件格式 |
|---------|------|---------|
| profile | `profile/` | `{id}.json` |
| procedural | `procedures/` | `{id}.json` |
| semantic | `patterns/` | `{id}.json` |
| episodic | `episodes/` | `{id}.json` |
| project | `projects/{key}/memories/` | `{id}.json` |
| candidate | `inbox/` | `{id}.json` |
