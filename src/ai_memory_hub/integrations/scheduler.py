"""
定时任务模块。

在 Windows (schtasks) 或 Unix (cron/launchd) 上安装定时任务，
定期运行记忆采集流水线。
"""

from __future__ import annotations

import os
import subprocess
import sys

from ai_memory_hub.core.config import load_config


def install_pipeline_task(*, interval_minutes: int = 60, task_name: str = "AI Memory Pipeline") -> dict:
    if interval_minutes < 1:
        return {
            "ok": False,
            "message": "interval_minutes must be greater than or equal to 1",
        }

    config = load_config()
    app_home = config.app_home_path.resolve()

    # Use platform-appropriate paths
    if sys.platform == "win32":
        script_path = app_home / "scripts" / "run-pipeline.py"
        python_path = app_home / ".venv" / "Scripts" / "python.exe"
    else:
        script_path = app_home / "scripts" / "run-pipeline.py"
        python_path = app_home / ".venv" / "bin" / "python"

    if not script_path.exists():
        return {
            "ok": False,
            "message": f"Missing task runner script: {script_path}",
        }
    if not python_path.exists():
        return {
            "ok": False,
            "message": f"Missing repo-local Python runtime: {python_path}",
        }

    env = os.environ.copy()
    env["AI_MEMORY_APP_HOME"] = str(app_home)
    env.setdefault("AI_MEMORY_HOME", str(config.data_home_path))

    # Windows: use schtasks
    if sys.platform == "win32":
        command = [
            "schtasks.exe",
            "/Create",
            "/F",
            "/SC",
            "MINUTE",
            "/MO",
            str(interval_minutes),
            "/TN",
            task_name,
            "/TR",
            f'"{python_path}" "{script_path}"',
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
        manual_command = " ".join(_quote_arg(part) for part in command)
        if completed.returncode != 0:
            return {
                "ok": False,
                "task_name": task_name,
                "interval_minutes": interval_minutes,
                "script_path": str(script_path),
                "manual_command": manual_command,
                "message": output or "Failed to create scheduled task.",
            }

        return {
            "ok": True,
            "task_name": task_name,
            "interval_minutes": interval_minutes,
            "script_path": str(script_path),
            "command": manual_command,
            "message": output or "Scheduled task created successfully.",
        }

    # macOS/Linux: use launchd or cron (provide instructions)
    if sys.platform == "darwin":
        plist_content = _generate_launchd_plist(task_name, str(python_path), str(script_path), interval_minutes)
        return {
            "ok": True,
            "task_name": task_name,
            "interval_minutes": interval_minutes,
            "script_path": str(script_path),
            "message": "On macOS, use launchd or cron for scheduled tasks. See CONTRIBUTING.md for setup instructions.",
            "launchd_plist": plist_content,
        }

    # Linux
    cron_cmd = f'*/{interval_minutes} * * * * {python_path} {script_path}'
    return {
        "ok": True,
        "task_name": task_name,
        "interval_minutes": interval_minutes,
        "script_path": str(script_path),
        "message": "On Linux, add to crontab:",
        "cron_line": cron_cmd,
    }


def _quote_arg(value: str) -> str:
    if " " in value or "\t" in value:
        return f'"{value}"'
    return value


def _generate_launchd_plist(task_name: str, python_path: str, script_path: str, interval_minutes: int) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{task_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval_minutes * 60}</integer>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
'''
