from pathlib import Path

from qdrant_client import QdrantClient

from tests.conftest import SAMPLE_FILES
from voxcode.codebase.chunker.chunker import build_chunks
from voxcode.codebase.indexer.index_store import IndexStore
from voxcode.codebase.indexer.indexer import RepositoryIndexer
from voxcode.codebase.indexer.scanner import scan_repository
from voxcode.retrieval.bm25.tokenizer import tokenize
from voxcode.retrieval.semantic.vector_store import VectorStore


def test_tokenizer_splits_identifiers():
    tokens = tokenize("getUserById(user_id) HTTPServer")
    assert {"getuserbyid", "get", "user", "by", "id", "user_id", "http", "server"} <= set(tokens)
    assert "the" not in tokenize("the user")


def test_python_chunks_split_methods_and_keep_module_code():
    chunks = build_chunks("app/auth.py", _dedent(SAMPLE_FILES["app/auth.py"]), "python")
    by_name = {c.symbol_name: c for c in chunks}
    assert {"AuthService", "AuthService.login", "AuthService.logout", "verify_password", "find_user"} <= set(
        by_name
    )
    assert by_name["AuthService.login"].chunk_type == "method"
    assert "def login" not in by_name["AuthService"].content  # class header only
    module = [c for c in chunks if c.chunk_type == "module"]
    assert module and "SECRET_SALT" in module[0].content
    assert "find_user" in by_name["AuthService.login"].calls


def test_typescript_and_markdown_chunks():
    ts = build_chunks("web/api.ts", _dedent(SAMPLE_FILES["web/api.ts"]), "typescript")
    names = {c.symbol_name for c in ts}
    assert {"UserClient", "UserClient.fetchUserProfile", "deleteAccount"} <= names
    md = build_chunks("docs/guide.md", _dedent(SAMPLE_FILES["docs/guide.md"]), "markdown")
    assert [c.symbol_name for c in md] == ["Guide", "Password reset"]


def test_long_functions_are_split():
    body = "def big():\n" + "\n".join(f"    x{i} = {i}" for i in range(250))
    chunks = build_chunks("big.py", body, "python", max_lines=100)
    assert [c.symbol_name for c in chunks] == ["big (part 1)", "big (part 2)", "big (part 3)"]
    assert chunks[1].start_line == 101


def test_scanner_skips_dependencies(sample_repo):
    files = {p.relative_to(sample_repo).as_posix() for p in scan_repository(sample_repo)}
    assert "app/auth.py" in files and "web/api.ts" in files
    assert not any(f.startswith("node_modules") for f in files)


def test_incremental_indexing_updates_vectors(sample_repo, tmp_path, fake_embedder):
    client = QdrantClient(":memory:")
    indexer = RepositoryIndexer(IndexStore(tmp_path / "idx"), lambda key: VectorStore(key, client=client))

    first = indexer.index(sample_repo, embedder=fake_embedder)
    assert first.files_changed == 3 and first.semantic_indexed
    store = VectorStore(first.repo_key, client=client)
    assert client.count(store.collection).count == first.chunks_created

    unchanged = indexer.index(sample_repo, embedder=fake_embedder)
    assert unchanged.files_changed == 0

    (sample_repo / "app/auth.py").write_text("def only_function():\n    return 1\n")
    (sample_repo / "docs/guide.md").unlink()
    second = indexer.index(sample_repo, embedder=fake_embedder)
    assert second.files_changed == 1 and second.files_removed == 1
    index = indexer.store.load(second.repo_key)
    assert client.count(store.collection).count == len(index.chunks)
    assert not any(c.file_path == "docs/guide.md" for c in index.chunks)


def test_repos_with_same_name_get_separate_indexes(tmp_path):
    from voxcode.core.repository import repository_key

    a, b = tmp_path / "one" / "repo", tmp_path / "two" / "repo"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert repository_key(a) != repository_key(b)
    assert repository_key(a).startswith("repo-")


def _dedent(text: str) -> str:
    import textwrap

    return textwrap.dedent(text).lstrip("\n")


def test_index_store_round_trip(tmp_path, sample_repo):
    store = IndexStore(tmp_path)
    stats = RepositoryIndexer(store).index(Path(sample_repo))
    assert store.list()[0]["chunks"] == stats.chunks_created


def test_ambiguous_folder_names_only_skipped_at_root(tmp_path):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("x = 1\n")
    (tmp_path / "src" / "storage").mkdir(parents=True)
    (tmp_path / "src" / "storage" / "blobs.py").write_text("def save():\n    pass\n")
    (tmp_path / "myenv").mkdir()
    (tmp_path / "myenv" / "pyvenv.cfg").write_text("home = /usr\n")
    (tmp_path / "myenv" / "lib.py").write_text("x = 1\n")
    files = {p.relative_to(tmp_path).as_posix() for p in scan_repository(tmp_path)}
    assert files == {"src/storage/blobs.py"}


def test_index_rebuilds_when_format_changes(sample_repo, tmp_path, monkeypatch):
    from voxcode.config.settings import settings

    indexer = RepositoryIndexer(IndexStore(tmp_path / "idx"))
    assert indexer.index(sample_repo).files_changed == 3
    assert indexer.index(sample_repo).files_changed == 0
    monkeypatch.setattr(settings, "MAX_CHUNK_LINES", 50)
    assert indexer.index(sample_repo).files_changed == 3


def test_corrupt_index_is_ignored(tmp_path):
    store = IndexStore(tmp_path)
    store.path("broken").write_text("{not json")
    assert store.load("broken") is None
