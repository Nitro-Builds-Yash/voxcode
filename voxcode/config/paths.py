"""Where VoxCode keeps its data.

Indexes, vectors and caches live outside your repositories, in
`VOXCODE_HOME` (default `~/.voxcode`), so nothing is ever written into the
code you index.
"""

from pathlib import Path

from voxcode.config.settings import settings

PACKAGE_DIR = Path(__file__).resolve().parents[1]

DATA_DIR = Path(settings.VOXCODE_HOME).expanduser()
INDEXES_DIR = DATA_DIR / "indexes"
QDRANT_DIR = DATA_DIR / "qdrant"
CACHE_DIR = DATA_DIR / "cache"


def ensure_storage_dirs() -> None:
    for directory in (DATA_DIR, INDEXES_DIR, QDRANT_DIR, CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
