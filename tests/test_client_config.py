import unittest
from pathlib import Path

from ai_memory_hub.integrations.client_config import build_mcp_client_config


class ClientConfigTests(unittest.TestCase):
    def test_build_codex_mcp_config_uses_toml_and_module_entrypoint(self) -> None:
        repo_root = Path(r"<TEST_REPO_ROOT>")

        payload = build_mcp_client_config(client="codex", repo_root=repo_root)

        self.assertEqual(payload["format"], "toml")
        self.assertTrue(payload["config_path"].endswith(r".codex\config.toml"))
        self.assertIn("[mcp_servers.ai-memory]", payload["snippet"])
        self.assertIn("'ai_memory_hub.cli'", payload["snippet"])
        self.assertIn("'run-mcp'", payload["snippet"])
        self.assertIn(str(repo_root), payload["snippet"])

    def test_build_claude_mcp_config_uses_json(self) -> None:
        repo_root = Path(r"<TEST_REPO_ROOT>")

        payload = build_mcp_client_config(client="claude", repo_root=repo_root)

        self.assertEqual(payload["format"], "json")
        self.assertIn('"mcpServers"', payload["snippet"])
        self.assertIn('"ai-memory"', payload["snippet"])


if __name__ == "__main__":
    unittest.main()
