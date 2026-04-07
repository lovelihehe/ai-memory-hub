import tempfile
import time
import unittest
from pathlib import Path

from ai_memory_hub.core.config import BootstrapProject, MemoryConfig
from ai_memory_hub.core.models import Evidence, MemoryRecord
from ai_memory_hub.pipeline.pipeline import run_index
from ai_memory_hub.services.wiki import build_wiki, lint_wiki
from ai_memory_hub.storage.db import MemoryStore


def _record(
    memory_id: str,
    title: str,
    memory_type: str,
    scope: str,
    project_key: str | None,
    tags: list[str],
    *,
    summary: str | None = None,
    evidence_excerpt: str = "strong evidence excerpt",
) -> MemoryRecord:
    evidence = []
    if evidence_excerpt is not None:
        evidence = [
            Evidence(
                source_tool="codex",
                source_path="session.jsonl",
                session_id="s1",
                timestamp="2026-04-07T00:00:00+00:00",
                excerpt=evidence_excerpt,
            )
        ]
    return MemoryRecord(
        id=memory_id,
        title=title,
        memory_type=memory_type,
        scope=scope,
        tool="shared",
        project_key=project_key,
        summary=summary or f"{title} summary",
        details=f"{title} details",
        evidence=evidence,
        confidence=0.9,
        stability=0.8,
        sensitivity="low",
        tags=tags,
        created_at="2026-04-07T00:00:00+00:00",
        last_seen_at="2026-04-07T00:00:00+00:00",
        reviewed_at=None,
        status="active",
        supersedes=None,
    )


class WikiBuildTests(unittest.TestCase):
    def _config_and_store(self) -> tuple[MemoryConfig, MemoryStore]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        config = MemoryConfig.default()
        config.data_home = str(tmp_path / "data")
        config.bootstrap_projects = [
            BootstrapProject(id="demo-proj", name="Demo Project", path="", tags=[], fact_templates=[])
        ]
        store = MemoryStore(config)
        store.ensure_layout()
        return config, store

    def test_build_wiki_uses_display_names_and_generates_topic_threshold(self) -> None:
        config, store = self._config_and_store()
        store.write_memory(_record("m1", "Repo Rule", "procedural", "global", None, ["workflow", "python"]))
        store.write_memory(_record("m2", "Repo Pattern", "semantic", "project", "demo-proj", ["python", "architecture"]))

        result = build_wiki(config, store, incremental=True)

        wiki_root = Path(result["wiki_root"])
        index_text = (wiki_root / "index.md").read_text(encoding="utf-8")
        project_text = (wiki_root / "projects" / "demo-proj" / "index.md").read_text(encoding="utf-8")

        self.assertIn("[[projects/demo-proj/index|Demo Project]]", index_text)
        self.assertIn("# Project Demo Project", project_text)
        self.assertTrue((wiki_root / "topics" / "python.md").exists())
        self.assertFalse((wiki_root / "topics" / "workflow.md").exists())

    def test_build_wiki_incremental_only_rewrites_impacted_pages(self) -> None:
        config, store = self._config_and_store()
        store.write_memory(_record("m1", "First Rule", "procedural", "global", None, ["python", "workflow"]))
        store.write_memory(_record("m2", "Project Pattern", "semantic", "project", "demo-proj", ["python", "architecture"]))

        first = build_wiki(config, store, incremental=True)
        wiki_root = Path(first["wiki_root"])
        project_page = wiki_root / "projects" / "demo-proj" / "index.md"
        type_page = wiki_root / "types" / "procedural.md"
        before_project_mtime = project_page.stat().st_mtime_ns
        before_type_mtime = type_page.stat().st_mtime_ns

        time.sleep(0.01)
        store.write_memory(_record("m2", "Project Pattern", "semantic", "project", "demo-proj", ["python", "architecture"], summary="updated summary"))
        second = build_wiki(config, store, incremental=True)

        after_project_mtime = project_page.stat().st_mtime_ns
        after_type_mtime = type_page.stat().st_mtime_ns

        self.assertFalse(second["wiki_full_rebuild"])
        self.assertGreater(after_project_mtime, before_project_mtime)
        self.assertEqual(after_type_mtime, before_type_mtime)

    def test_lint_reports_weak_evidence_duplicates_and_conflicts(self) -> None:
        config, store = self._config_and_store()
        store.write_memory(_record("m1", "Use Python Logging", "procedural", "project", "demo-proj", ["logging"], summary="Always use structured logging", evidence_excerpt="ok"))
        store.write_memory(_record("m2", "Use Python Logging", "procedural", "project", "demo-proj", ["logging"], summary="Never use structured logging", evidence_excerpt="tiny"))
        store.write_memory(_record("m3", "Logging Rule", "procedural", "project", "demo-proj", ["logging"], summary="Always use structured logging", evidence_excerpt=None))

        build_wiki(config, store, incremental=True)
        report = lint_wiki(config, store)
        counts = report["issue_counts"]

        self.assertGreaterEqual(counts.get("weak_evidence", 0), 2)
        self.assertGreaterEqual(counts.get("duplicate_concept", 0), 1)
        self.assertGreaterEqual(counts.get("conflicting_topic", 0), 1)

    def test_run_index_returns_wiki_summary(self) -> None:
        config, store = self._config_and_store()
        store.write_memory(_record("m1", "Repo Rule", "procedural", "global", None, ["python", "workflow"]))
        store.rebuild_memory_index(incremental=False)

        old_home = None
        import os
        old_home = os.environ.get("AI_MEMORY_HOME")
        os.environ["AI_MEMORY_HOME"] = config.data_home
        try:
            result = run_index(incremental=True)
        finally:
            if old_home is None:
                os.environ.pop("AI_MEMORY_HOME", None)
            else:
                os.environ["AI_MEMORY_HOME"] = old_home

        self.assertIn("wiki_lint_summary", result)
        self.assertIn("wiki_full_rebuild", result)


if __name__ == "__main__":
    unittest.main()
