#!/usr/bin/env python3
"""
定时任务脚本：跨平台执行 ai-memory pipeline。

由 Windows 任务计划程序 / macOS launchd / Linux cron 调用。
自动检测 OS，找对应的 .venv Python，设置正确的环境变量，然后执行 pipeline。

使用方式：
    # Windows 任务计划程序示例（install-tasks 命令自动生成）
    python.exe scripts\run-pipeline.py

    # Linux/macOS cron 示例
    0 * * * * /path/to/.venv/bin/python /path/to/scripts/run-pipeline.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent


def _venv_python() -> Path:
    """返回当前 OS 对应的 .venv Python 可执行文件路径。"""
    if sys.platform == "win32":
        return REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return REPO_ROOT / ".venv" / "bin" / "python"


def run_pipeline() -> None:
    """检测虚拟环境 → 设置环境变量 → 调用 ai-memory pipeline。"""
    venv_python = _venv_python()

    if not venv_python.exists():
        print(f"Error: Virtual environment not found at {REPO_ROOT / '.venv'}")
        print("Please run: pip install -e .")
        sys.exit(1)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 默认数据目录，优先级低于已设置的 AI_MEMORY_HOME
    env.setdefault("AI_MEMORY_HOME", str(Path.home() / "ai-memory"))
    env.setdefault("AI_MEMORY_APP_HOME", str(REPO_ROOT))

    result = os.spawnve(
        os.P_WAIT,
        str(venv_python),
        [str(venv_python), "-m", "ai_memory_hub.cli", "pipeline"],
        env,
    )
    sys.exit(result)


if __name__ == "__main__":
    run_pipeline()
