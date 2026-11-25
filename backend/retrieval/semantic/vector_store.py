"""Qdrant vector storage, one collection per repository."""

from __future__ import annotations

import threading

import numpy as np

from backend.config.paths import QDRANT_DIR
from backend.config.settings import settings

_client = None
_client_lock = threading.Lock()


def get_qdrant_client():
    """Shared Qdrant client: embedded on-disk store, or a Qdrant server."""
    global _client
    with _client_lock:
        if _client is None:
            from qdrant_client import QdrantClient

            if settings.QDRANT_MODE.lower() == "server":
                _client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
            else:
                QDRANT_DIR.mkdir(parents=True, exist_ok=True)
                _client = QdrantClient(path=str(QDRANT_DIR))
        return _client


class VectorStore:
    def __init__(self, repo_key: str, client=None):
        self.client = client or get_qdrant_client()
        self.collection = f"{settings.QDRANT_COLLECTION_PREFIX}_{repo_key}"

    def exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def ensure(self, dimension: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        if self.exists():
            info = self.client.get_collection(self.collection)
            params = info.config.params.vectors
            if getattr(params, "size", dimension) == dimension:
                return
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            self.collection, vectors_config=VectorParams(size=dimension, distance=Distance.COSINE)
        )

    def drop(self) -> None:
        if self.exists():
            self.client.delete_collection(self.collection)

    def upsert(self, ids: list[str], file_paths: list[str], vectors: np.ndarray) -> None:
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(id=i, vector=v.tolist(), payload={"file_path": f})
            for i, f, v in zip(ids, file_paths, vectors, strict=True)
        ]
        for start in range(0, len(points), 256):
            self.client.upsert(self.collection, points=points[start : start + 256])

    def delete_files(self, file_paths: list[str]) -> None:
        if not file_paths or not self.exists():
            return
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchAny

        for start in range(0, len(file_paths), 200):
            batch = file_paths[start : start + 200]
            self.client.delete(
                self.collection,
                points_selector=FilterSelector(
                    filter=Filter(must=[FieldCondition(key="file_path", match=MatchAny(any=batch))])
                ),
            )

    def search(self, vector: np.ndarray, top_k: int) -> list[tuple[str, float]]:
        if not self.exists():
            return []
        response = self.client.query_points(self.collection, query=vector.tolist(), limit=top_k)
        return [(str(point.id), float(point.score)) for point in response.points]
