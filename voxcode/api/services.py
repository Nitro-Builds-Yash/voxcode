"""Process-wide service singletons shared by the API routes."""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from pathlib import Path

from voxcode.agent.edit_agent import EditAgent
from voxcode.agent.memory.memory_manager import MemoryManager
from voxcode.agent.pipeline import AgentPipeline
from voxcode.agent.runtime import runtime_cache  # noqa: F401 (re-exported for scripts)
from voxcode.agent.tool_agent import ToolAgent
from voxcode.codebase.indexer.indexer import IndexStats, RepositoryIndexer
from voxcode.codebase.watcher.watcher import WatchManager
from voxcode.config.settings import settings
from voxcode.core.model_registry import ModelRegistry
from voxcode.core.repository import repository_key
from voxcode.voice.emotion.emotion_analyzer import EmotionAnalyzer, EmotionProfile, analyze_with_audio

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
    contextualizer = None
    if settings.CONTEXTUAL_CHUNKS:
        from voxcode.retrieval.contextual import ChunkContextualizer

        contextualizer = ChunkContextualizer(registry.get_provider())
    with _index_lock:
        stats = indexer.index(
            repo,
            embedder=registry.get_embedder(),
            full=full,
            contextualizer=contextualizer,
            precise_graph=not settings.BACKGROUND_CALL_GRAPH,
        )
    retrain_ranker(stats.repo_key)
    runtime_cache.invalidate(stats.repo_key)
    _schedule_call_graph(stats.repo_key)
    return stats


_index_lock = threading.RLock()
_graph_jobs: set[str] = set()


def _schedule_call_graph(repo_key: str) -> None:
    """Build precise call edges in the background (Jedi takes ~30 s on a mid-size repo)."""
    from voxcode.codebase.indexer.indexer import complete_call_graph

    index = indexer.store.load(repo_key)
    if index is None or not index.graph_pending or repo_key in _graph_jobs:
        return
    _graph_jobs.add(repo_key)

    def run() -> None:
        try:
            if complete_call_graph(indexer.store, repo_key, _index_lock):
                runtime_cache.invalidate(repo_key)
        except Exception as exc:  # the name-based graph keeps working
            logger.warning("Background call graph failed for %s: %s", repo_key, exc)
        finally:
            _graph_jobs.discard(repo_key)
            latest = indexer.store.load(repo_key)
            if latest is not None and latest.graph_pending:
                _schedule_call_graph(repo_key)  # files changed while it ran

    threading.Thread(target=run, daemon=True, name=f"call-graph-{repo_key}").start()


def retrain_ranker(repo_key: str) -> dict | None:
    from voxcode.learning.training import auto_train

    index = indexer.store.load(repo_key)
    report = auto_train(index) if index is not None else None
    if report and report.get("saved"):
        runtime_cache.invalidate(repo_key)
    return report


def ensure_indexed(repo: Path) -> str:
    """Index on first use so a fresh workspace works without a manual step."""
    key = repository_key(repo)
    if not indexer.store.path(key).exists():
        index_repository(repo)
    else:
        _schedule_call_graph(key)  # finish a call graph left pending (e.g. a process that exited)
    if settings.WATCH_ENABLED:
        watcher.watch(repo, index_repository)
    return key


def analyze_emotion(text: str, audio=None) -> EmotionProfile | None:
    """Tone from the transcript, refined by the voice when audio is given."""
    if not (settings.EMOTION_ENABLED and text):
        return None
    if audio is not None and len(audio):
        try:
            return analyze_with_audio(emotion_analyzer, text, audio)
        except Exception as exc:  # never let tone analysis break a request
            logger.debug("Prosody analysis failed: %s", exc)
    return emotion_analyzer.analyze(text)
