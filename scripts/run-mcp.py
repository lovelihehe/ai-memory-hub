#!/usr/bin/env python3
"""
MCP 服务器启动脚本：跨平台运行 ai-memory run-mcp。

由 MCP 客户端（如 Claude Desktop、Cursor）通过 MCP 协议调用。
自动检测 OS，找对应的 .venv Python，设置正确的环境变量，然后启动 MCP 服务器。

使用方式（在 Claude Desktop 等 MCP 客户端的配置文件中引用）：
    # Windows
    "python.exe", "scripts\\run-mcp.py"
    # Linux/macOS
    "/path/to/.venv/bin/python", "scripts/run-mcp.py"
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


def run_mcp() -> None:
    """检测虚拟环境 → 设置环境变量 → 调用 ai-memory run-mcp。"""
    venv_python = _venv_python()

    if not venv_python.exists():
        print(f"Error: Virtual environment not found at {REPO_ROOT / '.venv'}")
        print("Please run: pip install -e .")
        sys.exit(1)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("AI_MEMORY_HOME", str(Path.home() / "ai-memory"))
    env.setdefault("AI_MEMORY_APP_HOME", str(REPO_ROOT))

    result = os.spawnve(
        os.P_WAIT,
        str(venv_python),
        [str(venv_python), "-m", "ai_memory_hub.cli", "run-mcp"],
        env,
    )
    sys.exit(result)


if __name__ == "__main__":
    run_mcp()
