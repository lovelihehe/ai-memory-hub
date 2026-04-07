# Codex MCP Setup

> 相关文档：[ARCHITECTURE.md](./ARCHITECTURE.md) | [FAQ.md](./FAQ.md) | [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)

This project now includes a dedicated CLI helper for Codex MCP setup:

```bash
ai-memory mcp-config --client codex
```

The command prints a ready-to-paste TOML snippet for Codex. By default it targets:

- User-level config: `~/.codex/config.toml`
- Project-level config: `.codex/config.toml`

Example output on Windows:

```toml
[mcp_servers.ai-memory]
command = '<REPO_ROOT>\.venv\Scripts\python.exe'
args = ['scripts\run-mcp.py']
cwd = '<REPO_ROOT>'
```

> Replace `<REPO_ROOT>` with your actual repository path (e.g. `C:\Projects\ai-memory-hub`).
> Run `ai-memory mcp-config --client codex` to generate the real snippet for your setup.

After adding the snippet, restart Codex and run:

```bash
ai-memory mcp-self-check
```

If you also want JSON snippets for Claude Desktop or Cursor, use:

```bash
ai-memory mcp-config --client claude
ai-memory mcp-config --client cursor
```

## Notes

- `scripts/run-mcp.py` is still the only runtime entrypoint. The new command just generates client config safely.
- The generated `command` prefers the repo-local virtual environment so Codex does not depend on a system-wide Python on `PATH`.
- If `.venv` does not exist yet, run `pip install -e .` first.
