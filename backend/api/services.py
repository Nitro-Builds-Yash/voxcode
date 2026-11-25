"""Process-wide service singletons shared by the API routes."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from backend.agent.edit_agent import EditAgent
from backend.agent.memory.memory_manager import MemoryManager
from backend.agent.pipeline import AgentPipeline
from backend.agent.runtime import runtime_cache  # noqa: F401 (re-exported for scripts)
from backend.agent.tool_agent import ToolAgent
from backend.codebase.indexer.indexer import IndexStats, RepositoryIndexer
from backend.codebase.watcher.watcher import WatchManager
from backend.config.settings import settings
from backend.core.model_registry import ModelRegistry
from backend.core.repository import repository_key
from backend.voice.emotion.emotion_analyzer import EmotionAnalyzer, EmotionProfile

logger = logging.getLogger(__name__)

registry = ModelRegistry()
indexer = RepositoryIndexer()
watcher = WatchManager(settings.WATCH_DEBOUNCE_SECONDS)
emotion_analyzer = EmotionAnalyzer()


@lru_cache(maxsize=1)
def memory() -> MemoryManager:
    return MemoryManager()


def pipeline() -> AgentPipeline:
    return AgentPipeline(registry, memory())


def edit_agent() -> EditAgent:
    return EditAgent(registry, memory())


def tool_agent() -> ToolAgent:
    return ToolAgent(registry, memory())


def index_repository(repo: Path, full: bool = False) -> IndexStats:
    stats = indexer.index(repo, embedder=registry.get_embedder(), full=full)
    runtime_cache.invalidate(stats.repo_key)
    return stats


def ensure_indexed(repo: Path) -> str:
    """Index on first use so a fresh workspace works without a manual step."""
    key = repository_key(repo)
    if not indexer.store.path(key).exists():
        index_repository(repo)
    if settings.WATCH_ENABLED:
        watcher.watch(repo, index_repository)
    return key


def analyze_emotion(text: str) -> EmotionProfile | None:
    return emotion_analyzer.analyze(text) if settings.EMOTION_ENABLED and text else None
