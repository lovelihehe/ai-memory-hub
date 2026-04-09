"""
服务层模块：搜索、管理、统计、渲染和 Obsidian 集成。

面向用户的功能模块。
"""

from ai_memory_hub.services.search import memory_context, memory_search
from ai_memory_hub.services.manage import apply_feedback, list_memories
from ai_memory_hub.services.stats import memory_stats
from ai_memory_hub.services.render import render_outputs
from ai_memory_hub.services.obsidian import ensure_vault_layout, sync_obsidian_vault
from ai_memory_hub.services.wiki import build_wiki
from ai_memory_hub.services.dream import run_dream_generate, run_dream_for_session
from ai_memory_hub.services.brainstorming import run_brainstorming_sync
from ai_memory_hub.services.skill_service import SkillService, get_skill_service
from ai_memory_hub.services.profile_service import ProfileService, get_profile_service
from ai_memory_hub.services.usage_feedback import process_usage_feedback

__all__ = [
    # search
    "memory_context",
    "memory_search",
    # manage
    "apply_feedback",
    "list_memories",
    # stats
    "memory_stats",
    # render
    "render_outputs",
    # obsidian
    "ensure_vault_layout",
    "sync_obsidian_vault",
    # wiki
    "build_wiki",
    # dream
    "run_dream_generate",
    "run_dream_for_session",
    # brainstorming
    "run_brainstorming_sync",
    # skill
    "SkillService",
    "get_skill_service",
    # profile
    "ProfileService",
    "get_profile_service",
    # usage feedback
    "process_usage_feedback",
]
