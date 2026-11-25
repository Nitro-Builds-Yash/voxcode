"""Filesystem locations used by VoxCode."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
STORAGE_DIR = PROJECT_ROOT / "storage"
LOCAL_MODELS_DIR = PROJECT_ROOT / "local_models"
TESTS_DIR = PROJECT_ROOT / "tests"

INDEXES_DIR = STORAGE_DIR / "indexes"
QDRANT_DIR = STORAGE_DIR / "qdrant"
CACHE_DIR = STORAGE_DIR / "cache"
SESSIONS_DIR = STORAGE_DIR / "sessions"


def ensure_storage_dirs() -> None:
    for directory in (STORAGE_DIR, INDEXES_DIR, QDRANT_DIR, CACHE_DIR, SESSIONS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
