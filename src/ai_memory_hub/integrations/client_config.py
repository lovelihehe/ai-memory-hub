from __future__ import annotations

import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _venv_python(repo_root: Path) -> Path:
    if sys.platform == "win32":
        return repo_root / ".venv" / "Scripts" / "python.exe"
    return repo_root / ".venv" / "bin" / "python"


def _toml_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _json_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def build_mcp_client_config(
    *,
    client: str,
    server_name: str = "ai-memory",
    repo_root: Path | None = None,
) -> dict[str, str]:
    repo_root = (repo_root or _repo_root()).resolve()
    command = str(_venv_python(repo_root))
    mcp_args = ["-m", "ai_memory_hub.cli", "run-mcp"]
    cwd = str(repo_root)

    if client == "codex":
        snippet = "\n".join(
            [
                f"[mcp_servers.{server_name}]",
                f"command = {_toml_literal(command)}",
                "args = [" + ", ".join(_toml_literal(arg) for arg in mcp_args) + "]",
                f"cwd = {_toml_literal(cwd)}",
            ]
        )
        return {
            "client": client,
            "format": "toml",
            "config_path": str(Path.home() / ".codex" / "config.toml"),
            "project_config_path": str(repo_root / ".codex" / "config.toml"),
            "snippet": snippet,
        }

    snippet = "\n".join(
        [
            "{",
            '  "mcpServers": {',
            f'    "{server_name}": {{',
            f'      "command": {_json_string(command)},',
            '      "args": [' + ", ".join(_json_string(arg) for arg in mcp_args) + "],",
            f'      "cwd": {_json_string(cwd)}',
            "    }",
            "  }",
            "}",
        ]
    )

    config_path = "%APPDATA%\\Claude\\claude_desktop_config.json"
    if client == "cursor":
        config_path = "%APPDATA%\\Cursor\\User\\globalStorage\\saoudmeckami-mcp\\settings.json"

    return {
        "client": client,
        "format": "json",
        "config_path": config_path,
        "snippet": snippet,
    }
