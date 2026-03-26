"""
项目引导模块。

从 config.json 的 bootstrap_projects 配置初始化项目记忆。
支持 fact_templates 自动注入和 auto-detect 标签。
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from ai_memory_hub.core.config import load_config
from ai_memory_hub.extraction.extractors import write_memory_record
from ai_memory_hub.core.models import Evidence, utc_now
from ai_memory_hub.storage.db import MemoryStore
from ai_memory_hub.core.utils import project_key_from_path, stable_id


def bootstrap_known_projects(store: MemoryStore) -> dict[str, int]:
    """从 config.json 的 bootstrap_projects 配置引导项目"""
    config = load_config()
    bootstrapped = 0

    for project in config.bootstrap_projects:
        if not project.path:
            continue
        repo_path = Path(os.path.expanduser(project.path)).resolve()
        if not repo_path.exists():
            continue

        templates = list(project.fact_templates)
        if not templates and "auto-detect" in project.tags:
            templates = _auto_detect_fact_templates(repo_path)

        if templates:
            bootstrapped += _bootstrap_project(store, repo_path, project, templates)

    return {"bootstrapped_project_memories": bootstrapped}


def _bootstrap_project(
    store: MemoryStore,
    repo_path: Path,
    project: "BootstrapProject",
    templates: list[dict],
) -> int:
    """为一个项目写入 fact templates"""
    project_key = project_key_from_path(str(repo_path))
    if not project_key:
        project_key = stable_id("project", project.id)

    evidence = [
        Evidence(
            source_tool="system",
            source_path=str(repo_path),
            session_id=None,
            timestamp=utc_now(),
            excerpt=f"Bootstrapped from config: {project.name}",
        )
    ]

    written = 0
    for payload in templates:
        record_id = stable_id(
            payload.get("memory_type", "semantic"),
            "project",
            "shared",
            project_key,
            payload["title"].lower(),
            payload.get("summary", "").lower(),
        )
        current = store.load_memory(record_id)
        if current and current.manual_override:
            continue

        tags = sorted(set(payload.get("tags", []) + [project_key, "bootstrap"]))
        write_memory_record(
            store,
            title=payload["title"],
            memory_type=payload.get("memory_type", "semantic"),
            scope="project",
            tool="shared",
            project_key=project_key,
            summary=payload.get("summary", payload["title"]),
            details=payload.get("details", payload.get("summary", payload["title"])),
            tags=tags,
            evidence=[asdict(e) for e in evidence],
            confidence=0.98,
            stability=0.96,
            sensitivity="low",
            status="active",
            supersedes=(current.id if current else None),
            managed_by="system",
            manual_override=False,
        )
        written += 1
    return written


def _auto_detect_fact_templates(repo_path: Path) -> list[dict]:
    """基于项目特征文件自动推断 fact templates"""
    templates: list[dict] = []

    if (repo_path / "pom.xml").exists():
        templates.append({
            "title": f"{repo_path.name} is a Java/Maven project",
            "memory_type": "semantic",
            "summary": f"Build: mvn clean install | Run: mvn spring-boot:run -Pdev",
            "details": f"{repo_path.name} uses Maven for dependency management and build. Spring Boot application.",
            "tags": ["java", "maven", "spring-boot", "architecture"],
        })
    elif (repo_path / "package.json").exists():
        templates.append({
            "title": f"{repo_path.name} is a Node.js project",
            "memory_type": "semantic",
            "summary": f"Build: npm install | Run: npm start or npm run dev",
            "details": f"{repo_path.name} uses npm for dependency management.",
            "tags": ["javascript", "node", "npm", "architecture"],
        })
    elif (repo_path / "go.mod").exists():
        templates.append({
            "title": f"{repo_path.name} is a Go project",
            "memory_type": "semantic",
            "summary": f"Build: go build | Run: go run .",
            "details": f"{repo_path.name} is a Go module.",
            "tags": ["go", "golang", "architecture"],
        })
    elif (repo_path / "Cargo.toml").exists():
        templates.append({
            "title": f"{repo_path.name} is a Rust project",
            "memory_type": "semantic",
            "summary": f"Build: cargo build | Run: cargo run",
            "details": f"{repo_path.name} is a Rust project using Cargo.",
            "tags": ["rust", "cargo", "architecture"],
        })

    return templates


def bootstrap_project_facts(store: MemoryStore, repo_root: Path) -> int:
    """
    向后兼容函数：为指定目录 bootstrapping fact templates。
    优先用 auto-detect；若目录为空则写默认 project fact。
    """
    templates = _auto_detect_fact_templates(repo_root)
    if not templates:
        templates = [{
            "title": f"{repo_root.name} is a project",
            "memory_type": "semantic",
            "summary": f"{repo_root.name} — build file not detected yet",
            "details": f"Add project build files (pom.xml, package.json, go.mod, etc.) to enable auto-detection.",
            "tags": [repo_root.name, "bootstrap", "project"],
        }]
    fake_project = type("FakeProject", (), {
        "id": repo_root.name,
        "name": repo_root.name,
        "path": str(repo_root),
        "tags": [],
        "fact_templates": templates,
    })()
    return _bootstrap_project(store, repo_root, fake_project, templates)
