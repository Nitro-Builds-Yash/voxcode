"""Finds indexable source files, skipping dependencies and build output."""

from __future__ import annotations

import os
from pathlib import Path

from backend.codebase.parser.languages import detect_language

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "bower_components",
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".cache",
    "coverage",
    "htmlcov",
    ".idea",
    ".vscode",
    ".gradle",
    "vendor",
    "site-packages",
    ".eggs",
    "storage",
    "local_models",
}
IGNORED_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".map", ".lock")


def scan_repository(root: Path, max_file_bytes: int = 1_000_000) -> list[Path]:
    """Return indexable files under `root`, sorted, as absolute paths."""
    files: list[Path] = []
    for directory, subdirs, filenames in os.walk(root):
        subdirs[:] = sorted(d for d in subdirs if d not in IGNORED_DIRS and not d.endswith(".egg-info"))
        for filename in filenames:
            if filename.endswith(IGNORED_SUFFIXES) or detect_language(filename) is None:
                continue
            path = Path(directory) / filename
            try:
                if path.is_symlink() or path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            files.append(path)
    return sorted(files)
