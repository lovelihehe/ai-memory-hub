"""
AI Memory Hub 命令行入口。

用法示例：
    ai-memory init
    ai-memory pipeline
    ai-memory search --query "项目偏好"
    ai-memory context --tool codex --task-type implementation --query "数据库"

所有命令输出 JSON 格式，便于脚本调用和管道组合。
"""

from __future__ import annotations

import argparse
import json
import sys

from ai_memory_hub.core.config import load_config
from ai_memory_hub.extraction.quality import repair_data
from ai_memory_hub.integrations.doctor import run_doctor
from ai_memory_hub.pipeline.growth import memory_growth
from ai_memory_hub.services.manage import apply_feedback, batch_apply_feedback, list_memories
from ai_memory_hub.integrations.mcp_server import mcp_runtime_status, run_handshake_self_check
from ai_memory_hub.services.obsidian import ensure_vault_layout, sync_obsidian_vault
from ai_memory_hub.pipeline.pipeline import init_environment, run_collect, run_consolidate, run_index, run_pipeline
from ai_memory_hub.integrations.release import run_release_check
from ai_memory_hub.services.search import memory_context, memory_search
from ai_memory_hub.services.stats import memory_stats
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.integrations.scheduler import install_pipeline_task


def _print(payload) -> None:
    """将数据序列化为格式化的 JSON 输出到 stdout。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_error(payload) -> None:
    """将错误信息序列化为格式化的 JSON 输出到 stderr。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    """
    构建完整的命令行参数解析器。

    命令分为以下几类：
      - 流水线：init / collect / consolidate / index / pipeline
      - 检索：search / context / list / show
      - 审核：review / review-batch
      - 运维：doctor / repair-data / install-tasks
      - Obsidian：obsidian-sync
      - MCP：run-mcp / mcp-self-check
      - 工具：stats / growth / release-check
      - 数据：export / import
    """
    parser = argparse.ArgumentParser(prog="ai-memory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── 流水线类 ─────────────────────────────────────────────
    # 无额外参数的简单子命令，按名称批量注册
    for name in [
        "init",        # 初始化数据目录和环境
        "collect",     # 采集原始事件
        "consolidate", # 提炼候选记忆
        "index",       # 重建检索索引
        "pipeline",    # 执行完整流水线
        "obsidian-sync",  # 同步到 Obsidian Vault
        "run-mcp",     # 启动 MCP 服务器
        "mcp-self-check",  # MCP 握手自检
        "release-check",   # 发布前检查
        "doctor",      # 健康检查
        "repair-data", # 修复数据问题
    ]:
        subparsers.add_parser(name)

    # ── 定时任务 ──────────────────────────────────────────────
    install_tasks_parser = subparsers.add_parser("install-tasks")
    install_tasks_parser.add_argument("--interval-minutes", type=int, default=60)
    install_tasks_parser.add_argument("--task-name", default="AI Memory Pipeline")

    # ── 数据导入导出 ──────────────────────────────────────────
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output", required=True, help="Export file path")

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--input", required=True, help="Import file path")

    # ── 统计分析 ─────────────────────────────────────────────
    stats_parser = subparsers.add_parser("stats")
    stats_parser.add_argument("--top", type=int, default=10)

    # ── 搜索 ─────────────────────────────────────────────────
    # 支持按 scope（profile/procedural/semantic/episodic）、project、tool 过滤
    # --semantic 开启向量语义搜索，默认使用 FTS5 关键词搜索
    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--scope", default="all")
    search_parser.add_argument("--project")
    search_parser.add_argument("--tool", default="all")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--tags", nargs="*")
    search_parser.add_argument("--semantic", action="store_true", help="Use semantic similarity search")

    # ── 上下文生成 ────────────────────────────────────────────
    # 根据工具类型和任务类型生成相关的"必须遵循"和"已知模式"记忆
    context_parser = subparsers.add_parser("context")
    context_parser.add_argument("--tool", required=True)
    context_parser.add_argument("--repo")
    context_parser.add_argument("--task-type", required=True)
    context_parser.add_argument("--query", default="")

    # ── 记忆列表 ─────────────────────────────────────────────
    # 支持按 status（candidate/active/archived）、scope、tool、project 过滤
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status", default="all")
    list_parser.add_argument("--scope", default="all")
    list_parser.add_argument("--tool", default="all")
    list_parser.add_argument("--project")
    list_parser.add_argument("--limit", type=int, default=50)

    # ── 查看单条记忆 ─────────────────────────────────────────
    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--id", required=True)

    # ── 审核 ─────────────────────────────────────────────────
    # 支持六种操作：confirm（确认）/ promote（升为 active）/ demote（降为 candidate）
    #          archive（归档）/ contradict（标记矛盾）/ merge（合并到目标记忆）
    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("--id", required=True)
    review_parser.add_argument(
        "--action",
        required=True,
        choices=["confirm", "promote", "demote", "archive", "contradict", "merge"],
    )
    review_parser.add_argument("--target-id")

    # ── 批量审核 ──────────────────────────────────────────────
    # 支持按置信度阈值或记忆年龄过滤，--dry-run 只报告不执行
    review_batch_parser = subparsers.add_parser("review-batch")
    review_batch_parser.add_argument("--action", required=True, choices=["confirm", "demote", "archive"])
    review_batch_parser.add_argument("--min-confidence", type=float, default=0.0)
    review_batch_parser.add_argument("--by-age", type=int, default=0, help="Archive candidates older than N days")
    review_batch_parser.add_argument("--dry-run", action="store_true")

    # ── 成长分析 ─────────────────────────────────────────────
    growth_parser = subparsers.add_parser("growth")
    growth_parser.add_argument("--period", default="week", choices=["week", "month", "quarter"])

    return parser


def main(argv: list[str] | None = None) -> int:
    """
    命令分发入口。

    所有命令统一返回以下退出码：
      0 - 成功
      1 - 失败（含 MCP 依赖缺失等）

    成功时输出 JSON 到 stdout，失败时输出 JSON 到 stderr。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # ── 流水线 ──────────────────────────────────────────────
    if args.command == "init":
        _print(init_environment())
    elif args.command == "collect":
        _print(run_collect())
    elif args.command == "consolidate":
        _print(run_consolidate())
    elif args.command == "index":
        _print(run_index())
    elif args.command == "pipeline":
        _print(run_pipeline())

    # ── Obsidian ─────────────────────────────────────────────
    elif args.command == "obsidian-sync":
        config = load_config()
        store = MemoryStore(config)
        ensure_vault_layout(config)
        _print(sync_obsidian_vault(config, store))

    # ── 检索 ─────────────────────────────────────────────────
    elif args.command == "search":
        store = MemoryStore(load_config())
        _print(memory_search(
            store,
            query=args.query,
            scope=args.scope,
            tags=args.tags,
            project=args.project,
            tool=args.tool,
            limit=args.limit,
            semantic=args.semantic,
        ))
    elif args.command == "context":
        store = MemoryStore(load_config())
        _print(memory_context(
            store,
            tool=args.tool,
            repo=args.repo,
            task_type=args.task_type,
            query=args.query,
        ))
    elif args.command == "list":
        store = MemoryStore(load_config())
        _print(list_memories(
            store,
            status=args.status,
            scope=args.scope,
            tool=args.tool,
            project=args.project,
            limit=args.limit,
        ))
    elif args.command == "show":
        store = MemoryStore(load_config())
        record = store.load_memory(args.id)
        _print(record.to_dict() if record else None)

    # ── 统计与成长 ───────────────────────────────────────────
    elif args.command == "stats":
        store = MemoryStore(load_config())
        _print(memory_stats(store, top_n=args.top))
    elif args.command == "growth":
        store = MemoryStore(load_config())
        _print(memory_growth(store, period=args.period))

    # ── 审核 ─────────────────────────────────────────────────
    elif args.command == "review":
        store = MemoryStore(load_config())
        _print(apply_feedback(store, memory_id=args.id, action=args.action, target_id=args.target_id))
    elif args.command == "review-batch":
        store = MemoryStore(load_config())
        _print(batch_apply_feedback(
            store,
            action=args.action,
            min_confidence=args.min_confidence,
            by_age_days=args.by_age,
            dry_run=args.dry_run,
        ))

    # ── 运维 ─────────────────────────────────────────────────
    elif args.command == "release-check":
        _print(run_release_check())
    elif args.command == "doctor":
        _print(run_doctor())
    elif args.command == "repair-data":
        store = MemoryStore(load_config())
        _print(repair_data(store))
    elif args.command == "run-mcp":
        status = mcp_runtime_status()
        if not status["available"]:
            _print_error({
                "ok": False,
                "dependency": status["dependency"],
                "install_command": status["install_command"],
                "reason": status["reason"],
            })
            return 1
        from ai_memory_hub.integrations.mcp_server import main as mcp_main
        mcp_main()
    elif args.command == "mcp-self-check":
        _print(run_handshake_self_check())
    elif args.command == "install-tasks":
        _print(install_pipeline_task(interval_minutes=args.interval_minutes, task_name=args.task_name))

    # ── 数据 ─────────────────────────────────────────────────
    elif args.command == "export":
        store = MemoryStore(load_config())
        from pathlib import Path
        _print(store.export_data(Path(args.output)))
    elif args.command == "import":
        store = MemoryStore(load_config())
        from pathlib import Path
        _print(store.import_data(Path(args.input)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
