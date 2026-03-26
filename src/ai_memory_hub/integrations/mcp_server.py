"""
MCP 服务器模块。

通过 Model Context Protocol 暴露记忆工具：
- memory_search: 搜索记忆
- memory_get: 获取单条记忆
- memory_write: 写入新记忆
- memory_apply_feedback: 应用反馈
- run_pipeline: 触发流水线
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from io import TextIOWrapper
from typing import Any

import anyio
import anyio.lowlevel
import mcp.types as types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from ai_memory_hub.core.config import load_config
from ai_memory_hub.extraction.extractors import write_memory_record
from ai_memory_hub.services.manage import apply_feedback
from ai_memory_hub.pipeline.pipeline import init_environment, run_pipeline
from ai_memory_hub.services.search import memory_context, memory_search
from ai_memory_hub.storage.db import MemoryStore
from mcp.shared.message import SessionMessage


def mcp_runtime_status() -> dict[str, Any]:
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("mcp"):
            return {
                "available": False,
                "dependency": "mcp",
                "install_command": "python -m pip install 'mcp>=1.12.4,<2'",
                "reason": "The MCP runtime dependency is not installed, so the MCP server cannot start.",
            }
        raise
    return {
        "available": True,
        "dependency": "mcp",
    }


def _store() -> MemoryStore:
    return MemoryStore(load_config())


def memory_search_tool(
    query: str,
    scope: str = "all",
    tags: list[str] | None = None,
    project: str | None = None,
    tool: str = "all",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search local memories by query and optional metadata filters."""
    return memory_search(_store(), query=query, scope=scope, tags=tags, project=project, tool=tool, limit=limit)


def memory_get(memory_id: str) -> dict[str, Any] | None:
    """Get a memory record by id."""
    store = _store()
    record = store.load_memory(memory_id)
    if record:
        store.update_memory_access(memory_id)
    return record.to_dict() if record else None


def memory_write_tool(
    mode: str,
    title: str,
    memory_type: str,
    scope: str,
    tool: str,
    summary: str,
    details: str,
    project_key: str | None = None,
    tags: list[str] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    confidence: float = 0.9,
    stability: float = 0.8,
    sensitivity: str = "low",
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Write a candidate or confirmed memory record."""
    status = "active" if mode == "confirmed" else "candidate"
    record = write_memory_record(
        _store(),
        title=title,
        memory_type=memory_type,
        scope=scope,
        tool=tool,
        project_key=project_key,
        summary=summary,
        details=details,
        tags=tags or [],
        evidence=evidence or [],
        confidence=confidence,
        stability=stability,
        sensitivity=sensitivity,
        status=status,
        supersedes=supersedes,
    )
    _store().rebuild_memory_index()
    return record.to_dict()


def memory_context_tool(tool: str, repo: str | None, task_type: str, query: str = "") -> dict[str, Any]:
    """Build a structured context bundle for the current task."""
    result = memory_context(_store(), tool=tool, repo=repo, task_type=task_type, query=query)

    # 检查候选记忆中有无值得升格的，附加为建议
    suggestions = _build_suggestions(_store())
    if suggestions:
        result["suggestions"] = suggestions

    return result


def _build_suggestions(store: MemoryStore) -> list[dict]:
    """检查候选记忆中有无值得升格的，附加为建议"""
    from ai_memory_hub.services.manage import list_memories
    candidates = list_memories(store, status="candidate", limit=50)
    suggestions = []
    for c in candidates:
        if 0.70 <= c["confidence"] <= 0.84:
            suggestions.append({
                "id": c["id"],
                "title": c["title"],
                "confidence": c["confidence"],
                "evidence_count": len(c.get("evidence", [])),
            })
    return sorted(suggestions, key=lambda x: x["confidence"], reverse=True)[:5]


def memory_apply_feedback_tool(memory_id: str, action: str, target_id: str | None = None) -> dict[str, Any]:
    """Promote, demote, merge, archive, or contradict a memory."""
    return apply_feedback(_store(), memory_id=memory_id, action=action, target_id=target_id)


def memory_refresh() -> dict[str, Any]:
    """Run the collector, consolidator, and indexer pipeline."""
    return run_pipeline()


def _build_mcp():
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("AI Memory Hub")
    app.tool(name="memory_search")(memory_search_tool)
    app.tool(name="memory_get")(memory_get)
    app.tool(name="memory_write")(memory_write_tool)
    app.tool(name="memory_context")(memory_context_tool)
    app.tool(name="memory_feedback")(memory_apply_feedback_tool)
    app.tool(name="memory_refresh")(memory_refresh)
    return app


@asynccontextmanager
async def _adaptive_stdio_server(
    stdin: anyio.AsyncFile[str] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
):
    if not stdin:
        stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8", newline=""))
    if not stdout:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline=""))

    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)
    protocol_mode: dict[str, str | None] = {"value": None}

    async def _send_parsed_message(payload: str) -> None:
        try:
            message = types.JSONRPCMessage.model_validate_json(payload)
        except Exception as exc:
            await read_stream_writer.send(exc)
            return
        await read_stream_writer.send(SessionMessage(message))

    async def stdin_reader():
        try:
            async with read_stream_writer:
                while True:
                    line = await stdin.readline()
                    if line == "":
                        return
                    if line in ("\n", "\r\n"):
                        continue

                    if line.lower().startswith("content-length:"):
                        protocol_mode["value"] = protocol_mode["value"] or "content-length"
                        headers: dict[str, str] = {}
                        header_line = line
                        while True:
                            name, _, value = header_line.partition(":")
                            headers[name.strip().lower()] = value.strip()
                            header_line = await stdin.readline()
                            if header_line == "":
                                return
                            if header_line in ("\n", "\r\n"):
                                break

                        content_length = headers.get("content-length")
                        if not content_length:
                            await read_stream_writer.send(
                                ValueError("Missing Content-Length header in MCP stdio message.")
                            )
                            continue

                        body = await stdin.read(int(content_length))
                        await _send_parsed_message(body)
                        continue

                    protocol_mode["value"] = protocol_mode["value"] or "jsonline"
                    await _send_parsed_message(line)
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def stdout_writer():
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    payload = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    if protocol_mode["value"] == "content-length":
                        await stdout.write(f"Content-Length: {len(payload.encode('utf-8'))}\r\n\r\n{payload}")
                    else:
                        await stdout.write(payload + "\n")
                    await stdout.flush()
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        tg.start_soon(stdin_reader)
        tg.start_soon(stdout_writer)
        yield read_stream, write_stream


async def _run_adaptive_stdio_server() -> None:
    app = _build_mcp()
    async with _adaptive_stdio_server() as (read_stream, write_stream):
        await app._mcp_server.run(
            read_stream,
            write_stream,
            app._mcp_server.create_initialization_options(),
        )


def run_handshake_self_check(timeout_seconds: float = 5.0) -> dict[str, Any]:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai-memory-self-check", "version": "0.1"},
        },
    }
    payload = json.dumps(request, ensure_ascii=False) + "\n"
    command = [sys.executable, "-m", "ai_memory_hub.cli", "run-mcp"]
    env = os.environ.copy()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(payload)
        process.stdin.flush()

        response_line = process.stdout.readline()
        if response_line == "":
            raise RuntimeError("MCP process closed stdout before returning initialize response.")
        response = json.loads(response_line)
        process.terminate()
        try:
            _, stderr_data = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr_data = process.communicate()
        return {
            "ok": response.get("id") == 1 and "result" in response,
            "command": command,
            "response": response,
            "stderr": stderr_data,
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()


def run_mcp() -> None:
    """运行 MCP 服务器入口点"""
    status = mcp_runtime_status()
    if not status["available"]:
        raise RuntimeError(status["reason"])
    init_environment()
    anyio.run(_run_adaptive_stdio_server)


def main() -> None:
    run_mcp()


if __name__ == "__main__":
    main()
