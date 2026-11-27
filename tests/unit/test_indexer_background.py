"""Regression: the first question waited ~30-50 s for the Jedi call graph."""

import time

from voxcode.config.settings import settings


def test_index_returns_before_the_call_graph_and_fills_it_in(isolated_storage, sample_repo, monkeypatch):
    from voxcode.api import services
    from voxcode.codebase.graph import precise

    real = precise.build_call_edges
    calls = []

    def slow(*args, **kwargs):
        calls.append(time.time())
        time.sleep(1.0)
        return real(*args, **kwargs)

    monkeypatch.setattr("voxcode.codebase.indexer.indexer.build_call_edges", slow)
    monkeypatch.setattr(settings, "BACKGROUND_CALL_GRAPH", True)
    started = time.time()
    stats = services.index_repository(sample_repo)
    assert time.time() - started < 0.9  # did not wait for the slow graph
    index = services.indexer.store.load(stats.repo_key)
    assert index.graph_pending and not index.call_edges

    deadline = time.time() + 10
    while time.time() < deadline:
        index = services.indexer.store.load(stats.repo_key)
        if not index.graph_pending:
            break
        time.sleep(0.05)
    assert not index.graph_pending and index.call_edges and calls


def test_edges_survive_for_unchanged_files():
    from voxcode.codebase.indexer.indexer import _surviving_edges
    from voxcode.models.code_chunk import CodeChunk

    def chunk(cid, path):
        return CodeChunk(chunk_id=cid, file_path=path, language="python", chunk_type="function",
                         symbol_name=cid, start_line=1, end_line=2, content="")  # fmt: skip

    chunks = [chunk("a", "x.py"), chunk("b", "y.py"), chunk("c", "z.py")]
    edges = {"a": ["b", "c"], "b": ["c"], "gone": ["a"]}
    assert _surviving_edges(chunks, {"y.py"}, edges) == {"a": ["c"]}
