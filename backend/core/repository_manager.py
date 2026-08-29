from __future__ import annotations
import os
import json
import logging
from typing import Dict, Any, Optional, List
from backend.config.paths import STORAGE_PATH

logger = logging.getLogger(__name__)


class RepositoryManager:
    """
    Tracks and manages indexed repositories, storage paths, and index state.
    """

    def __init__(self, storage_dir: Optional[str] = None):
        self.storage_dir = storage_dir or str(STORAGE_PATH)
        self.manifest_path = os.path.join(self.storage_dir, "repositories_manifest.json")
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> Dict[str, Any]:
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load repository manifest: {e}")
        return {}

    def _save_manifest(self):
        os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2)

    def register_repository(self, name: str, path: str, stats: Optional[Dict[str, Any]] = None):
        self.manifest[name] = {
            "name": name,
            "path": os.path.abspath(path),
            "stats": stats or {},
            "status": "indexed",
        }
        self._save_manifest()

    def get_repository(self, name: str) -> Optional[Dict[str, Any]]:
        return self.manifest.get(name)

    def list_repositories(self) -> List[Dict[str, Any]]:
        return list(self.manifest.values())

    def remove_repository(self, name: str):
        if name in self.manifest:
            del self.manifest[name]
            self._save_manifest()
