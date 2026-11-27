"""The repo-trained semantic model (LSA) used when no neural embedder is installed."""

import numpy as np

from voxcode.config.settings import settings
from voxcode.models.code_chunk import CodeChunk
from voxcode.retrieval.semantic.local_lsa import (
    LocalSemanticModel,
    LocalSemanticRetriever,
    model_for,
    randomized_svd,
)

TOPICS = {
    "auth": "login password session token user verify hash credential logout",
    "cache": "cache entry expire evict lru ttl store hit miss memory",
    "render": "render template html page view layout css markup widget",
    "billing": "invoice payment refund charge customer receipt tax currency",
}


def chunks(n_per_topic=12):
    rng = np.random.default_rng(1)
    out = []
    for topic, words in TOPICS.items():
        vocab = words.split()
        for i in range(n_per_topic):
            text = " ".join(rng.choice(vocab, 12))
            out.append(CodeChunk(chunk_id=f"{topic}{i}", file_path=f"app/{topic}_{i}.py", language="python",
                                 chunk_type="function", symbol_name=f"{topic}_{i}", start_line=1, end_line=3,
                                 content=f"def {topic}_{i}():\n    # {text}\n"))  # fmt: skip
    return out


def test_finds_related_code_without_the_exact_word():
    items = chunks()
    model = LocalSemanticModel.train([c.content for c in items], dimensions=8)
    retriever = LocalSemanticRetriever(model, items)
    # "credential expire" mixes topics; a pure "credential" query should land in auth
    top = retriever.search("credential", 5)
    assert top and all(r.chunk_id.startswith("auth") for r in top)
    assert retriever.search("zzz unknown words", 5) == []


def test_tiny_corpora_are_skipped():
    assert LocalSemanticModel.train(["def a(): pass", "def b(): pass"]) is None


def test_model_is_cached_per_chunk_set(isolated_storage, monkeypatch, tmp_path):
    from voxcode.config import paths

    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    items = chunks()
    first = model_for("repo", items)
    calls = []
    monkeypatch.setattr(LocalSemanticModel, "train", classmethod(lambda cls, *a, **k: calls.append(1)))
    second = model_for("repo", items)
    assert not calls and np.allclose(first.doc_vectors, second.doc_vectors)
    assert len(list((tmp_path / "cache" / "lsa").glob("repo-*.npz"))) == 1


def test_randomized_svd_matches_exact_on_a_small_matrix():
    rng = np.random.default_rng(0)
    m = rng.standard_normal((40, 30)) @ np.diag(np.linspace(5, 0.1, 30)) @ rng.standard_normal((30, 30))
    _, s, _ = randomized_svd(m, 5, oversample=20, power=6)
    assert np.allclose(s, np.linalg.svd(m, compute_uv=False)[:5], rtol=1e-3)


def test_hybrid_uses_local_semantic_without_an_embedder(isolated_storage, sample_repo, monkeypatch):
    from voxcode.api import services
    from voxcode.retrieval.hybrid.hybrid_retriever import HybridRetriever

    for i, (topic, words) in enumerate(TOPICS.items()):  # enough code for the model to learn from
        for j in range(4):
            (sample_repo / f"{topic}_{j}.py").write_text(
                f"def {topic}_{j}():\n    # {words}\n    return {i}\n"
            )
    index = services.indexer.store.load(services.indexer.index(sample_repo).repo_key)
    monkeypatch.setattr(settings, "LOCAL_SEMANTIC", True)
    retriever = HybridRetriever(index)
    assert type(retriever.semantic).__name__ == "LocalSemanticRetriever"
    assert any(r.source == "semantic" for r in retriever.candidate_lists("credential").semantic)
    monkeypatch.setattr(settings, "LOCAL_SEMANTIC", False)
    assert HybridRetriever(index).semantic is None
