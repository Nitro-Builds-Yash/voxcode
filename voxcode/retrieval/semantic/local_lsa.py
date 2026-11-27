"""A semantic model that trains itself on the repository: no download, no GPU.

Latent semantic analysis over code-aware tokens: a sublinear TF-IDF matrix
of the chunks (identifiers split into words), reduced with a randomized
truncated SVD. Words that appear in the same code end up close together,
so "router" finds the cascade module and "decision" finds `wait()`, which
plain keyword search misses. Trains in about a second for a mid-size repo
and is cached per index version.

Used for hybrid search when no neural embedding model is installed (the
`ml` extra). On VoxCode's 30-question benchmark, BM25 + this model reaches
MRR@10 0.54 vs 0.48 for BM25 alone (recall@5 0.70 vs 0.67).
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from collections import Counter
from pathlib import Path

import numpy as np

from voxcode.config import paths
from voxcode.models.code_chunk import CodeChunk
from voxcode.models.retrieval_result import RetrievalResult
from voxcode.retrieval.bm25.tokenizer import tokenize

logger = logging.getLogger(__name__)

DIMENSIONS = 128
MIN_DF = 2  # a word must appear in two chunks to say anything about similarity
MAX_DF = 0.5  # words in most chunks carry no topic


def randomized_svd(matrix, k: int, oversample: int = 10, power: int = 2, seed: int = 0):
    """Truncated SVD of a (sparse) matrix: Halko, Martinsson & Tropp (2011)."""
    rng = np.random.default_rng(seed)
    n_cols = matrix.shape[1]
    sketch = matrix @ rng.standard_normal((n_cols, k + oversample)).astype(np.float32)
    for _ in range(power):  # power iterations sharpen the spectrum
        sketch, _ = np.linalg.qr(sketch)
        sketch = matrix @ (matrix.T @ sketch)
    q, _ = np.linalg.qr(sketch)
    small = np.asarray((matrix.T @ q).T)
    u_small, s, vt = np.linalg.svd(small, full_matrices=False)
    return (q @ u_small)[:, :k], s[:k], vt[:k]


def truncated_svd(matrix, k: int):
    """Exact top-k singular triplets of a sparse matrix (ARPACK), largest first.

    A randomized SVD with the usual 2 power iterations lost most of the gain on
    VoxCode's benchmark (MRR 0.49 vs 0.54 exact), so the exact solver is used;
    the randomized one remains the fallback.
    """
    try:
        from scipy.sparse.linalg import svds

        u, s, vt = svds(matrix.astype(np.float64), k=k, random_state=0)
        order = np.argsort(-s)
        return u[:, order], s[order], vt[order]
    except Exception as exc:  # ARPACK can fail to converge on degenerate inputs
        logger.info("Exact SVD failed (%s); using randomized SVD", exc)
        return randomized_svd(matrix, k, oversample=40, power=6)


class LocalSemanticModel:
    def __init__(
        self, vocab: dict[str, int], idf: np.ndarray, projection: np.ndarray, doc_vectors: np.ndarray
    ):
        self.vocab, self.idf, self.projection, self.doc_vectors = vocab, idf, projection, doc_vectors

    @staticmethod
    def _weights(tokens: list[str], vocab: dict[str, int], idf: np.ndarray) -> dict[int, float]:
        return {
            vocab[t]: (1.0 + math.log(n)) * idf[vocab[t]] for t, n in Counter(tokens).items() if t in vocab
        }

    @classmethod
    def train(cls, texts: list[str], dimensions: int = DIMENSIONS) -> LocalSemanticModel | None:
        from scipy.sparse import csr_matrix

        docs = [tokenize(t) for t in texts]
        n = len(docs)
        df = Counter(tok for d in docs for tok in set(d))
        terms = sorted(t for t, c in df.items() if c >= MIN_DF and c <= MAX_DF * n)
        k = min(dimensions, n - 1, len(terms) - 1)
        if k < 8:
            return None  # too small to learn anything; BM25 alone is fine
        vocab = {t: i for i, t in enumerate(terms)}
        idf = np.array([math.log((1 + n) / (1 + df[t])) + 1.0 for t in terms], dtype=np.float32)
        rows, cols, vals = [], [], []
        for i, d in enumerate(docs):
            weights = cls._weights(d, vocab, idf)
            norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
            for j, w in weights.items():
                rows.append(i)
                cols.append(j)
                vals.append(w / norm)
        matrix = csr_matrix((np.asarray(vals, np.float32), (rows, cols)), shape=(n, len(terms)))
        u, s, vt = truncated_svd(matrix, k)
        docs_k = (u * s).astype(np.float32)
        docs_k /= np.linalg.norm(docs_k, axis=1, keepdims=True) + 1e-9
        return cls(vocab, idf, vt.T.astype(np.float32), docs_k)

    def embed_query(self, query: str) -> np.ndarray | None:
        weights = self._weights(tokenize(query), self.vocab, self.idf)
        if not weights:
            return None
        vec: np.ndarray = np.zeros(len(self.vocab), dtype=np.float32)
        for j, w in weights.items():
            vec[j] = w
        projected = vec @ self.projection
        norm = np.linalg.norm(projected)
        return projected / norm if norm else None

    def scores(self, query: str) -> np.ndarray | None:
        vector = self.embed_query(query)
        return None if vector is None else self.doc_vectors @ vector

    # ---------------------------------------------------------- persistence
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        terms = np.array(sorted(self.vocab, key=lambda t: self.vocab[t]))
        np.savez_compressed(
            path, terms=terms, idf=self.idf, projection=self.projection, docs=self.doc_vectors
        )

    @classmethod
    def load(cls, path: Path) -> LocalSemanticModel | None:
        try:
            data = np.load(path)
            vocab = {str(t): i for i, t in enumerate(data["terms"])}
            return cls(vocab, data["idf"], data["projection"], data["docs"])
        except (OSError, ValueError, KeyError):
            return None


def chunk_text(chunk: CodeChunk) -> str:
    return f"{chunk.symbol_name} {chunk.file_path} {chunk.context} {chunk.content}"


_lock = threading.Lock()


def model_for(repo_key: str, chunks: list[CodeChunk]) -> LocalSemanticModel | None:
    """Trained model for these chunks, from the disk cache when the chunks are unchanged."""
    digest = hashlib.sha1(usedforsecurity=False)
    for chunk in chunks:
        digest.update(chunk.chunk_id.encode())
        digest.update(hashlib.sha1(chunk_text(chunk).encode(), usedforsecurity=False).digest())
    path = paths.CACHE_DIR / "lsa" / f"{repo_key}-{digest.hexdigest()[:16]}.npz"
    with _lock:
        model = LocalSemanticModel.load(path) if path.exists() else None
        if model is not None and model.doc_vectors.shape[0] == len(chunks):
            return model
        try:
            model = LocalSemanticModel.train([chunk_text(c) for c in chunks])
        except ImportError:
            logger.info("scipy is not installed; local semantic search disabled")
            return None
        if model is not None:
            for old in path.parent.glob(f"{repo_key}-*.npz"):
                old.unlink(missing_ok=True)  # one cached model per repository
            model.save(path)
        return model


class LocalSemanticRetriever:
    """Same interface as SemanticRetriever, backed by the repo-trained model."""

    def __init__(self, model: LocalSemanticModel, chunks: list[CodeChunk]):
        self.model, self.chunks = model, chunks

    def search(self, query: str, top_k: int = 10) -> list[RetrievalResult]:
        from voxcode.retrieval.bm25.bm25_retriever import to_result

        scores = self.model.scores(query)
        if scores is None:
            return []
        top = np.argpartition(-scores, min(top_k, len(scores) - 1))[:top_k]
        top = top[np.argsort(-scores[top], kind="stable")]
        return [to_result(self.chunks[i], float(scores[i]), "semantic") for i in top if scores[i] > 0]
