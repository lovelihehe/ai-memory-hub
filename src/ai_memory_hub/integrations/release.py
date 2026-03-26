"""
发布前检查模块。

验证代码库状态，确保：
- 核心命令都已实现
- 废弃的 Windows 命令已移除
- 文档与代码同步
- 无多余噪音文件
"""

from __future__ import annotations

from pathlib import Path


LEGACY_WINDOWS_COMMANDS = ["install-codex", "install-claude"]
LEGACY_WINDOWS_TOKENS = ["install-codex", "install-claude", "install.ps1"]
EXPECTED_CORE_COMMANDS = [
    "init",
    "collect",
    "consolidate",
    "index",
    "pipeline",
    "install-tasks",
    "run-mcp",
    "mcp-config",
    "doctor",
    "repair-data",
    "stats",
    "search",
    "context",
    "list",
    "show",
    "review",
    "release-check",
]
NOISE_DIRECTORIES = [
    ".tmp-data",
    ".tmp-home",
    ".tmp-home-flags",
    ".tmp-home-merge",
    "venv",
    ".venv",
    "Microsoft",
    "pip",
]
LEGACY_WINDOWS_SCRIPT_PATHS = [
    "scripts/install.ps1",
    "scripts/run-collector.cmd",
    "scripts/run-consolidate.cmd",
    "scripts/run-index.cmd",
]
DOC_SCAN_PATHS = [
    "README.md",
    "BUILD.md",
    "docs/AI-HANDBOOK.md",
    "src/ai_memory_hub/cli.py",
    "pyproject.toml",
]


def run_release_check(*, root: Path | None = None) -> dict:
    project_root = (root or Path(__file__).resolve().parents[3]).resolve()

    checks = [
        _check_core_commands(project_root),
        _check_removed_windows_commands(project_root),
        _check_removed_windows_scripts(project_root),
        _check_docs_are_current(project_root),
        _check_noise_directories(project_root),
    ]
    status_counts = {"passed": 0, "warning": 0, "failed": 0}
    for check in checks:
        status_counts[check["status"]] += 1
    overall = "passed" if status_counts["failed"] == 0 else "failed"
    if overall == "passed" and status_counts["warning"] > 0:
        overall = "warning"

    return {
        "summary": {
            "root": str(project_root),
            "overall": overall,
            "passed": status_counts["passed"],
            "warning": status_counts["warning"],
            "failed": status_counts["failed"],
        },
        "checks": checks,
    }


def _check_core_commands(root: Path) -> dict:
    from ai_memory_hub.cli import build_parser

    parser = build_parser()
    subparsers_action = next(
        action for action in parser._actions if getattr(action, "dest", None) == "command"
    )
    commands = sorted(subparsers_action.choices.keys())
    missing = [name for name in EXPECTED_CORE_COMMANDS if name not in commands]
    unexpected = [name for name in LEGACY_WINDOWS_COMMANDS if name in commands]
    status = "passed" if not missing and not unexpected else "failed"
    details = []
    if missing:
        details.append(f"missing commands: {', '.join(missing)}")
    if unexpected:
        details.append(f"unexpected legacy commands: {', '.join(unexpected)}")
    return {
        "name": "core_commands",
        "status": status,
        "details": details or ["core CLI commands are present and legacy installers are absent"],
        "commands": commands,
    }


def _check_removed_windows_commands(root: Path) -> dict:
    cli_path = root / "src" / "ai_memory_hub" / "cli.py"
    content = cli_path.read_text(encoding="utf-8")
    hits = [token for token in LEGACY_WINDOWS_COMMANDS if token in content]
    return {
        "name": "removed_windows_commands",
        "status": "passed" if not hits else "failed",
        "details": ["legacy Windows install commands removed from CLI source"] if not hits else [f"found legacy commands in CLI source: {', '.join(hits)}"],
    }


def _check_removed_windows_scripts(root: Path) -> dict:
    leftovers = [path for path in LEGACY_WINDOWS_SCRIPT_PATHS if (root / path).exists()]
    return {
        "name": "removed_windows_scripts",
        "status": "passed" if not leftovers else "failed",
        "details": ["Windows-only helper scripts are absent"] if not leftovers else [f"leftover scripts: {', '.join(leftovers)}"],
    }


def _check_docs_are_current(root: Path) -> dict:
    hits: list[str] = []
    for relative_path in DOC_SCAN_PATHS:
        path = root / relative_path
        if not path.exists():
            hits.append(f"missing required doc/config file: {relative_path}")
            continue
        content = path.read_text(encoding="utf-8")
        for token in LEGACY_WINDOWS_TOKENS:
            if token in content:
                hits.append(f"{relative_path}: {token}")
    return {
        "name": "docs_current",
        "status": "passed" if not hits else "failed",
        "details": ["README and build docs are free of legacy installer references"] if not hits else hits,
    }


def _check_noise_directories(root: Path) -> dict:
    present = [name for name in NOISE_DIRECTORIES if (root / name).exists()]
    if not present:
        return {
            "name": "noise_directories",
            "status": "passed",
            "details": ["no temporary or platform-specific noise directories detected"],
        }

    warnings = [name for name in present if name in {"venv", ".venv"}]
    failures = [name for name in present if name not in {"venv", ".venv"}]
    if failures:
        return {
            "name": "noise_directories",
            "status": "failed",
            "details": [f"unexpected noise directories present: {', '.join(failures)}"]
            + ([f"virtual environment directories present: {', '.join(warnings)}"] if warnings else []),
        }
    return {
        "name": "noise_directories",
        "status": "warning",
        "details": [f"virtual environment directories present: {', '.join(warnings)}"],
    }
