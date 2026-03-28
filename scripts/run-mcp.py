#!/usr/bin/env python3
"""
Launch the AI Memory Hub MCP server.

This wrapper stays compatible with client configs that point to
`scripts/run-mcp.py`, but avoids an extra spawned Python process when already
running under the repo virtualenv. That makes stdio MCP handshakes reliable on
Windows clients such as Codex.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent


def _venv_python() -> Path:
    if sys.platform == "win32":
        return REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return REPO_ROOT / ".venv" / "bin" / "python"


def run_mcp() -> None:
    venv_python = _venv_python()

    if not venv_python.exists():
        print(f"Error: Virtual environment not found at {REPO_ROOT / '.venv'}", file=sys.stderr)
        print("Please run: pip install -e .", file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("AI_MEMORY_HOME", str(Path.home() / "ai-memory"))
    env.setdefault("AI_MEMORY_APP_HOME", str(REPO_ROOT))
    os.environ.update(env)

    current_python = Path(sys.executable).resolve()
    if current_python != venv_python.resolve():
        os.execve(
            str(venv_python),
            [str(venv_python), str(Path(__file__).resolve())],
            env,
        )

    src_dir = REPO_ROOT / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from ai_memory_hub.integrations.mcp_server import run_mcp as _run_mcp

    _run_mcp()


if __name__ == "__main__":
    run_mcp()
