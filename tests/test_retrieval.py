from qdrant_client import QdrantClient

from backend.codebase.indexer.index_store import IndexStore
from backend.codebase.indexer.indexer import RepositoryIndexer
from backend.generation.context.context_builder import ContextBuilder
from backend.models.retrieval_result import RetrievalResult
from backend.retrieval.hybrid.fusion import reciprocal_rank_fusion
from backend.retrieval.hybrid.hybrid_retriever import HybridRetriever
from backend.retrieval.semantic.vector_store import VectorStore


def _r(cid, source="bm25", path="a.py"):
    return RetrievalResult(
        chunk_id=cid, file_path=path, symbol_name=cid, content=f"code {cid}", source=source
    )


def test_rrf_rewards_agreement():
    fused = reciprocal_rank_fusion([[_r("a", "semantic"), _r("b", "semantic")], [_r("b"), _r("c")]])
    assert [r.chunk_id for r in fused] == ["b", "a", "c"]
    assert fused[0].source == "hybrid"


def _retriever(sample_repo, tmp_path, embedder):
    client = QdrantClient(":memory:")
    indexer = RepositoryIndexer(IndexStore(tmp_path / "idx"), lambda key: VectorStore(key, client=client))
    stats = indexer.index(sample_repo, embedder=embedder)
    index = indexer.store.load(stats.repo_key)
    return HybridRetriever(index, embedder, VectorStore(stats.repo_key, client=client))


def test_hybrid_search_finds_symbols(sample_repo, tmp_path, fake_embedder):
    retriever = _retriever(sample_repo, tmp_path, fake_embedder)
    assert retriever.semantic is not None
    results = retriever.search("verify the password hash", 5)
    assert results[0].symbol_name == "verify_password"
    assert retriever.search("fetchUserProfile", 3)[0].file_path == "web/api.ts"


def test_graph_expansion_adds_callees(sample_repo, tmp_path):
    retriever = _retriever(sample_repo, tmp_path, None)
    login = [r for r in retriever.search("login", 5) if r.symbol_name == "AuthService.login"]
    expanded = retriever.expand(login, 4)
    added = {r.symbol_name for r in expanded if r.source == "graph"}
    assert {"find_user", "verify_password"} <= added


def test_bm25_only_when_no_embedder(sample_repo, tmp_path):
    retriever = _retriever(sample_repo, tmp_path, None)
    assert retriever.semantic is None
    assert retriever.search("logout session")[0].symbol_name == "AuthService.logout"


def test_context_builder_respects_token_budget():
    chunks = [_r(str(i), path=f"f{i}.py").model_copy(update={"content": "x = 1\n" * 200}) for i in range(10)]
    built = ContextBuilder(max_tokens=1200).build("question", chunks)
    assert built.context_size <= 1200
    assert 0 < built.num_chunks < 10
    assert "   1| x = 1" in built.context
