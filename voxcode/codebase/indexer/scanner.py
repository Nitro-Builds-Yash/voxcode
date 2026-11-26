"""Finds indexable source files, skipping dependencies and build output."""

from __future__ import annotations

import os
from pathlib import Path

from voxcode.codebase.parser.languages import EXTENSION_LANGUAGES, detect_language
from voxcode.native import native

# Never source code, wherever they appear.
IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
    ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache", "htmlcov", ".idea",
    ".vscode", ".gradle", "site-packages", ".eggs",
}  # fmt: skip

# Usually generated output or data, but common names for real source folders
# deeper in a tree (e.g. `src/storage/`), so only skipped at the repo root.
ROOT_IGNORED_DIRS = {
    "dist", "build", "out", "target", "coverage", "vendor", "env", "storage", "local_models",
}  # fmt: skip

IGNORED_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".map", ".lock")


def _skip_dir(name: str, parent: Path, root: Path) -> bool:
    if name in IGNORED_DIRS or name.endswith(".egg-info"):
        return True
    if parent == root and name in ROOT_IGNORED_DIRS:
        return True
    # Virtual environments with unusual names.
    return (parent / name / "pyvenv.cfg").exists()


def scan_repository(root: Path, max_file_bytes: int = 1_000_000) -> list[Path]:
    """Return indexable files under `root`, sorted, as absolute paths."""
    accel = native()
    if accel is not None:
        return accel.scan_repository(
            Path(root),
            max_file_bytes,
            IGNORED_DIRS,
            ROOT_IGNORED_DIRS,
            list(IGNORED_SUFFIXES),
            set(EXTENSION_LANGUAGES),
        )
    return scan_repository_python(root, max_file_bytes)


def scan_repository_python(root: Path, max_file_bytes: int = 1_000_000) -> list[Path]:
    files: list[Path] = []
    for directory, subdirs, filenames in os.walk(root):
        parent = Path(directory)
        subdirs[:] = sorted(d for d in subdirs if not _skip_dir(d, parent, root))
        for filename in filenames:
            if filename.endswith(IGNORED_SUFFIXES) or detect_language(filename) is None:
                continue
            path = parent / filename
            try:
                if path.is_symlink() or path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            files.append(path)
    return sorted(files)
