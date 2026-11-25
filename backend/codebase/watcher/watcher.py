"""Re-index repositories automatically when files change (debounced)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.codebase.indexer.scanner import IGNORED_DIRS, ROOT_IGNORED_DIRS
from backend.codebase.parser.languages import detect_language

logger = logging.getLogger(__name__)


class WatchManager:
    def __init__(self, debounce_seconds: float = 2.0):
        self.debounce = debounce_seconds
        self._observers: dict[str, Any] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def is_watching(self, repo: Path) -> bool:
        return str(repo) in self._observers

    def watch(self, repo: Path, on_change: Callable[[Path], object]) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning("watchdog is not installed; file watching disabled")
            return False

        key = str(repo)
        with self._lock:
            if key in self._observers:
                return True

            manager = self

            class Handler(FileSystemEventHandler):
                def on_any_event(self, event):
                    if event.is_directory:
                        return
                    for raw in (event.src_path, getattr(event, "dest_path", "") or ""):
                        if raw and manager._relevant(repo, Path(raw)):
                            manager._schedule(repo, on_change)
                            return

            observer = Observer()
            observer.schedule(Handler(), key, recursive=True)
            observer.daemon = True
            observer.start()
            self._observers[key] = observer
        logger.info("Watching %s for changes", repo)
        return True

    def unwatch(self, repo: Path) -> None:
        with self._lock:
            observer = self._observers.pop(str(repo), None)
            timer = self._timers.pop(str(repo), None)
        if timer:
            timer.cancel()
        if observer:
            observer.stop()

    def stop_all(self) -> None:
        for key in list(self._observers):
            self.unwatch(Path(key))

    @staticmethod
    def _relevant(repo: Path, path: Path) -> bool:
        try:
            parts = path.relative_to(repo).parts
        except ValueError:
            return False
        dirs = parts[:-1]
        if dirs and dirs[0] in ROOT_IGNORED_DIRS:
            return False
        return not any(p in IGNORED_DIRS for p in dirs) and detect_language(path) is not None

    def _schedule(self, repo: Path, on_change: Callable[[Path], object]) -> None:
        key = str(repo)

        def fire():
            with self._lock:
                self._timers.pop(key, None)
            try:
                on_change(repo)
            except Exception:
                logger.exception("Re-index after file change failed for %s", repo)

        with self._lock:
            if key in self._timers:
                self._timers[key].cancel()
            timer = threading.Timer(self.debounce, fire)
            timer.daemon = True
            self._timers[key] = timer
            timer.start()
